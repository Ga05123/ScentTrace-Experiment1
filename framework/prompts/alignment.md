# Task
Align the target range of the recorded trajectory with the supplied Local Goals.

# Input
instruction is the complete original task. goal_catalog is the same task-derived catalog in every window. target_range gives inclusive global Unit indices to label. units contains that range and adjacent context, retaining all member actions and native pre/post observations. evidence_ref identifies an attached observation or action. Context Units outside target_range are not output targets.

Compact action display, when present: code_segments retains non-press code text and ordered_key_runs encodes literal pyautogui.press calls in source order. Each character in per_character_keys is one original press; special keys remain separate key entries. This is a display encoding, not pyautogui.write, a new action, or a new Unit. Original scripts and source mappings are retained outside the model input.

# Rules
1. Read the supplied Units in order and determine which subgoal each target Unit serves, including preparation, attempts, errors, and recovery. A single-goal catalog still requires this check. Do not change the Goal catalog.
2. Return consecutive, non-overlapping ranges covering target_range exactly once. Change Goal when the activity changes the subgoal it serves. A window boundary, new page, tool, or action type alone does not imply a Goal change. Use adjacent context to interpret the boundary.
3. Follow the complete Alignment Codebook included below. ALIGNED: exactly one supported primary Goal. SUPPORT_NO_GOAL: identifiable support or end control without a particular Goal. OFF_GOAL: positive evidence of substantive activity serving none of the user Goals. SUPPORT_NO_GOAL/OFF_GOAL have empty goal_ids. UNRESOLVED: primary attribution remains uncertain; retain supported candidate IDs (zero, one or several) and explain the missing distinction. Do not assume distant behavior that is absent from the input.
4. Preserve the original Unit boundary even for compound functions. Bare termination is SUPPORT_NO_GOAL; substantive answer/action termination is aligned by its content. Give a brief, concrete reason based on the supplied actions and observations. Preserve compound members and missing observations. Do not assign Operators.

# Output
Return JSON only, with ranges with inclusive first/last indices, goal_ids, status, reason. Use global Unit indices. Descriptions are English.

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
  ]
}
```

Output:
```json
{
  "ranges": [
    {
      "first": 0,
      "last": 1,
      "goal_ids": [],
      "status": "UNRESOLVED",
      "reason": "The interface and text-entry destination are unobserved, so search and note-taking Goals cannot be distinguished."
    }
  ]
}
```
