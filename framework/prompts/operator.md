# Task
Label the Operator of each target Unit within the supplied continuous Goal context.

# Input
instruction is the original task; goal_catalog contains its subgoals. alignment contains the saved Goal ranges for the supplied Units. target_range identifies the global Unit indices to label. units contains a continuous Goal interval, or a fixed window within it, plus adjacent context. Member actions and native pre/post observations are retained; context can cross a Goal boundary.

Compact action display, when present: code_segments retains non-press code text and ordered_key_runs encodes literal pyautogui.press calls in source order. Each character in per_character_keys is one original press; special keys remain separate key entries. This is a display encoding, not pyautogui.write, a new action, or a new Unit. Original scripts and source mappings are retained outside the model input.

# Rules
1. Use the supplied Goal alignment as context; do not revise Goal IDs, membership, or boundaries. If it prevents a reliable Operator judgment, return UNRESOLVED and explain the problem in reason. An UNRESOLVED alignment does not by itself determine the Operator. OFF_GOAL cannot receive COMMIT or LEAVE; apply the actual observable INSPECT/EXECUTE/SUPPORT function, or explain a genuinely unsupported distinction. Do not automatically fill a category gap with UNRESOLVED.
2. Inspect the member actions and attached pre/post observations. Identify the information candidate, search branch, or task object needed to apply the Codebook. A Goal change alone is not LEAVE. Anchor the Operator and reason to the current target Unit's own member actions and corresponding pre/post observation references. Neighboring Units may clarify intent, but their clicks, typing, commands, application states or results must not be described as actions or outcomes of the current Unit. A plausible Operator does not excuse an ungrounded description in reason.
3. Follow the complete Operator Codebook included below. Assign INSPECT, COMMIT, LEAVE, EXECUTE, SUPPORT, or UNRESOLVED. Give a brief, concrete reason. For COMMIT, identify what was adopted and the observable adoption. For LEAVE, identify the former branch, unresolved information need, and exit or replacement. For UNRESOLVED, state the missing distinction. Describe compound functions, a bounded adoption interval, or an upstream problem in the same reason field.
4. COMMIT marks the first observable adoption resolving the relevant information need; do not count the same adoption twice. LEAVE requires observable departure from an identifiable information space while the same need remains unresolved. Bare termination is SUPPORT, while answer-bearing termination requires its substantive function. Do not impose a new canonical label on the pending adoption-then-answer boundary.
5. Return one result for every Unit in target_range, in source order. Neighboring Units only provide context. If distant evidence needed for the distinction is absent, retain UNRESOLVED.

# Output
Return JSON only: units with unit_index, operator, reason. Descriptions are English.

# Example
Input:
```json
{
  "instruction": "Find a hotel under $100 with free parking, then save its address in notes.",
  "goal_catalog": {
    "goals": [
      {
        "goal_id": "G1",
        "goal_type": "SEEK",
        "description": "Find and select a hotel that meets the requirements.",
        "constraints": [
          "Price under $100.",
          "Free parking."
        ],
        "completion_condition": "A matching hotel and its address are identified.",
        "depends_on": []
      },
      {
        "goal_id": "G2",
        "goal_type": "ACT",
        "description": "Save the selected hotel address in notes.",
        "constraints": [],
        "completion_condition": "The selected hotel address is saved in notes.",
        "depends_on": [
          "G1"
        ]
      }
    ]
  },
  "target_range": {
    "first": 0,
    "last": 1
  },
  "units": [
    {
      "unit_index": 0,
      "members": [
        {
          "member_index": 0,
          "action": {
            "kind": "CODE",
            "content": {
              "code": "pyautogui.click(200, 300)"
            }
          },
          "action_ref": "U0000.M00.action",
          "terminal_event": false,
          "pre": {
            "status": "MISSING",
            "modality": "NATIVE_GUI",
            "evidence_ref": "U0000.M00.pre"
          },
          "post": {
            "status": "MISSING",
            "modality": "NATIVE_GUI",
            "evidence_ref": "U0000.M00.post"
          }
        }
      ]
    },
    {
      "unit_index": 1,
      "members": [
        {
          "member_index": 0,
          "action": {
            "kind": "CODE",
            "content": {
              "code": "pyautogui.write('Hotel A')"
            }
          },
          "action_ref": "U0001.M00.action",
          "terminal_event": false,
          "pre": {
            "status": "MISSING",
            "modality": "NATIVE_GUI",
            "evidence_ref": "U0001.M00.pre"
          },
          "post": {
            "status": "MISSING",
            "modality": "NATIVE_GUI",
            "evidence_ref": "U0001.M00.post"
          }
        }
      ]
    }
  ],
  "alignment": [
    {
      "first": 0,
      "last": 1,
      "goal_ids": [],
      "status": "UNRESOLVED"
    }
  ]
}
```

Output:
```json
{
  "units": [
    {
      "unit_index": 0,
      "operator": "UNRESOLVED",
      "reason": "Missing native observations prevent identifying the clicked object or text-entry function."
    },
    {
      "unit_index": 1,
      "operator": "UNRESOLVED",
      "reason": "Missing native observations prevent identifying the clicked object or text-entry function."
    }
  ]
}
```
