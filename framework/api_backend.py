"""Small provider boundary. No GPU service, SDK auto-retry, or text-only fallback."""
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class APIError(RuntimeError):
    def __init__(self, message, *, status=None, retry_after=None, ambiguous=False):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.ambiguous = ambiguous


class AuthenticationError(APIError): pass
class RateLimitError(APIError): pass
class APITimeout(APIError): pass
class ContextLimitError(APIError): pass
class ImageUnsupportedError(APIError): pass
class StructuredOutputUnsupportedError(APIError): pass
class APIResponseError(APIError): pass
class NetworkError(APIError): pass


def strict_json_loads(text):
    def unique_object(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: '+key)
            result[key] = value
        return result
    return json.loads(text,object_pairs_hook=unique_object)


_dispatch_lock = threading.Lock()
_next_dispatch = 0.0


def pace(interval):
    global _next_dispatch
    with _dispatch_lock:
        delay=max(0, _next_dispatch-time.monotonic())
        if delay: time.sleep(delay)
        _next_dispatch=time.monotonic()+interval


def defer_rate_limit(seconds):
    global _next_dispatch
    with _dispatch_lock:
        _next_dispatch=max(_next_dispatch,time.monotonic()+seconds)


def safe_text(text, model):
    """Provider error/output echoes must not persist the environment credential."""
    token = os.environ.get(model.get('api_key_env', ''), '')
    text = str(text)
    if token:
        text = text.replace(token, '[REDACTED]')
    return re.sub(r'(?i)Bearer\s+[^\s"\']+', 'Bearer [REDACTED]', text)


def sanitize(value, model):
    if isinstance(value, str): return safe_text(value, model)
    if isinstance(value, dict): return {k: sanitize(v, model) for k, v in value.items()}
    if isinstance(value, list): return [sanitize(v, model) for v in value]
    return value


def validate_config(model, *, inference=False):
    if any(k in model for k in ('api_key', 'token', 'authorization', 'headers')):
        raise ValueError('API credentials must be environment variables, never configuration fields')
    url = urllib.parse.urlparse(model['base_url'])
    if url.username or url.password or url.query or url.fragment:
        raise ValueError('base_url must not contain credentials, query parameters or fragments')
    if url.scheme not in {'http', 'https'} or not url.netloc:
        raise ValueError('base_url must be an absolute HTTP(S) URL')
    if model.get('backend', 'openai_chat_completions') not in {'openai_chat_completions', 'custom_provider'}:
        raise ValueError('Unknown API backend')
    if model.get('annotation_authority', 'CLOSED_SOURCE_MODEL') not in {'CLOSED_SOURCE_MODEL', 'API_MODEL'}:
        raise ValueError('API model attribution must not claim Human Gold')
    if set(model.get('provider_parameters', {})) - {'enable_thinking', 'thinking_budget', 'reasoning_effort'}:
        raise ValueError('Undocumented provider-specific parameter; use the isolated adapter entry')
    capabilities = model['capabilities']
    if capabilities['structured_output'] != 'json_schema':
        raise ValueError('Experiment 1 requires complete JSON Schema; output downgrade is not supported')
    if model.get('token_parameter', 'max_tokens') not in {'max_tokens', 'max_completion_tokens'}:
        raise ValueError('Use the provider documented token-limit parameter')
    if model['timeout_seconds'] <= 0:
        raise ValueError('timeout_seconds must be positive')
    if any(type(model['max_tokens'][s]) is not int or model['max_tokens'][s] < 1
           for s in ('goals', 'alignment', 'operator')):
        raise ValueError('Positive per-stage output token limits required')
    if inference:
        if model['model_id'].startswith('UNSELECTED'):
            raise ValueError('Select the approved API model before inference')
        if not os.environ.get(model['api_key_env']):
            raise AuthenticationError('Missing API key environment variable: ' + model['api_key_env'])


def build_body(request, model, messages, dynamic_schema):
    validate_config(model)
    if request['images'] and not model['capabilities']['images']:
        raise ImageUnsupportedError('Native GUI images required; configured API cannot receive images')
    if model.get('backend', 'openai_chat_completions') == 'custom_provider':
        return custom_provider('build_body', request=request, model=model,
                               messages=messages, schema=dynamic_schema)
    body = {'model': model['model_id'], 'messages': messages,
            model.get('token_parameter', 'max_tokens'): model['max_tokens'][request['stage']]}
    if model.get('temperature') is not None: body['temperature'] = model['temperature']
    body['response_format'] = {'type': 'json_schema', 'json_schema': {
        'name': 'scenttrace_' + request['stage'],
        'strict': True, 'schema': dynamic_schema}}
    body.update(model.get('provider_parameters', {}))
    return body


def custom_provider(operation, **kwargs):
    """Independent entry for a non-compatible provider, pending its actual docs.

    Implement build_body and complete here when authorized. Do not alter evidence
    selection, Codebooks or the annotation pipeline to adapt transport.
    """
    raise NotImplementedError('Non-compatible provider requires its interface documentation: ' + operation)


def http_error(status, text, retry_after=None):
    low = text.lower()
    if status in (401, 403): cls = AuthenticationError
    elif status == 429: cls = RateLimitError
    elif status == 413 or any(s in low for s in ('context_length', 'context length', 'maximum context', 'too many tokens', 'context window')):
        cls = ContextLimitError
    elif any(s in low for s in ('image', 'vision', 'image_url')) and any(
            s in low for s in ('not support', 'unsupported', 'invalid', 'not allowed', 'only text')):
        cls = ImageUnsupportedError
    elif any(s in low for s in ('response_format', 'json_schema', 'structured output')):
        cls = StructuredOutputUnsupportedError
    else: cls = APIError
    return cls('API HTTP ' + str(status) + ': ' + text[:1000], status=status,
               retry_after=retry_after, ambiguous=status >= 500)


def complete(body, model, *, interval=0, rate_limit_retries=0):
    """Retry only explicitly rejected 429 requests, with a finite shared cooldown.

    Successful, timed-out or otherwise ambiguous requests are never retried here.
    Defaults perform no transport retry. Semantic input/body stays unchanged.
    """
    if interval < 0 or interval > 60 or rate_limit_retries not in {0,1,2}:
        raise ValueError('API interval must be 0..60 seconds; rate_limit_retries 0..2')
    rejected=[]
    for attempt in range(rate_limit_retries+1):
        pace(interval)
        try:
            raw, request_id = complete_once(body,model)
            return raw, request_id, rejected
        except RateLimitError as exc:
            rejected.append({'http_status':429,'reason':str(exc),'retry_after':exc.retry_after})
            if attempt==rate_limit_retries: raise
            try: cooldown=float(exc.retry_after) if exc.retry_after is not None else 60.0
            except ValueError: cooldown=60.0
            # Longer provider cooldowns remain explicit failures rather than retrying early.
            if cooldown > 60: raise
            defer_rate_limit(max(0,cooldown))


def complete_once(body, model):
    validate_config(model, inference=True)
    if model.get('backend', 'openai_chat_completions') == 'custom_provider':
        return custom_provider('complete', body=body, model=model)
    endpoint = model['base_url'].rstrip('/') + '/chat/completions'
    headers = {'Content-Type': 'application/json',
               'Authorization': 'Bearer ' + os.environ[model['api_key_env']]}
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode('utf-8'), headers=headers)
    local = urllib.parse.urlparse(endpoint).hostname in {'127.0.0.1', 'localhost', '::1'}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=model['timeout_seconds']) as response:
            raw_bytes = response.read()
            request_id = response.headers.get('x-request-id')
    except urllib.error.HTTPError as exc:
        text = safe_text(exc.read().decode('utf-8', errors='replace'), model)
        raise http_error(exc.code, text, exc.headers.get('Retry-After')) from None
    except (TimeoutError, socket.timeout):
        raise APITimeout('API timeout; outcome/charge unknown. No automatic duplicate retry.', ambiguous=True) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise APITimeout('API timeout; outcome/charge unknown. No automatic duplicate retry.', ambiguous=True) from None
        raise NetworkError(safe_text(exc.reason, model), ambiguous=True) from None
    try:
        raw = raw_bytes.decode('utf-8')
        value = strict_json_loads(raw)
    except (ValueError, UnicodeError) as exc:
        error = APIResponseError('Invalid UTF-8/JSON API envelope; response may have been billed: '+safe_text(exc,model), ambiguous=True)
        error.raw_response = safe_text(raw_bytes.decode('utf-8',errors='replace'), model)
        error.provider_request_id = request_id
        raise error from None
    if not isinstance(value, dict):
        error = APIResponseError('API envelope must be an object', ambiguous=True)
        error.raw_response = sanitize(value, model)
        error.provider_request_id = request_id
        raise error
    return sanitize(value, model), request_id


def content_text(raw):
    try:
        if not isinstance(raw['choices'],list) or len(raw['choices']) != 1:
            raise APIResponseError('Expected one Chat Completions choice; no silent selection')
        choice = raw['choices'][0]
        message = choice['message']
        if message.get('refusal'):
            raise APIResponseError('Provider refused the request: ' + str(message['refusal']))
        text = message['content']
        if isinstance(text, list):
            if not all(isinstance(p,dict) and p.get('type')=='text' and isinstance(p.get('text'),str) for p in text):
                raise APIResponseError('Unexpected response content blocks; no silent filtering')
            text = ''.join(p['text'] for p in text)
        if not isinstance(text, str): raise TypeError('Missing text response')
        return text, choice.get('finish_reason')
    except (KeyError, IndexError, TypeError, AttributeError):
        raise APIResponseError('Invalid Chat Completions response envelope; no parseable content', ambiguous=True) from None
