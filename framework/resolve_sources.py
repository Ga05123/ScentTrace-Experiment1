"""Resolve the existing source package's image paths; never import its old framework."""
import argparse
import json
from pathlib import Path
import experiment1 as e
import sources


def resolve_record(record, package):
    record = json.loads(json.dumps(record))
    for event in record['events']:
        for role in ('pre', 'post'):
            path = event[role]['path']
            if path:
                event[role]['path'] = str((Path(package) / path).resolve())
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-package', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    package = Path(args.source_package).resolve()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    records = [resolve_record(record, package)
               for _,record in sources.lines(package/'normalized_qualified_relative.jsonl')]
    for record in records:
        e.validate_source(record)
    # No overwrite of an existing prepared source or dataset.
    with output.open('x', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    print('RESOLVED_SOURCE_TRAJECTORIES', len(records), 'MODEL_CALLS', 0)


if __name__ == '__main__':
    main()
