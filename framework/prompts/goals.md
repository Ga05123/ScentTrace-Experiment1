# Task
Extract Local Goals from the original user instruction, applying the complete Goal Codebook included below.

# Input and permitted evidence
instruction is the complete original task. source_segments are verbatim numbered pieces that concatenate to it; segment boundaries locate sources, not Goals. No trajectory, actor, action, image, thought, reward or success is provided or permitted at this stage.

# Rules
1. Follow the Goal Codebook for SEEK/ACT/MIXED, independent semantic outcomes, constraints, completion conditions and necessary dependencies. A legitimate MIXED Goal does not automatically create an issue.
2. Do not invent Goals from an imagined execution plan or treat each interface operation as a requested outcome.
3. Assign sequential G1, G2, ... in instruction order. depends_on contains necessary earlier Goals. source_ids uses only supplied IDs, once each in source order; multiple Goals may share a source segment.
4. Record substantive uncertainty in issues. Valid source IDs do not prove complete semantic coverage; never fabricate a Goal to fill unreferenced text.

# Output
Return JSON only: goals and issues. Each Goal has goal_id, goal_type, description, constraints, completion_condition, depends_on and source_ids. Descriptions are English, source_ids unchanged. Follow the supplied JSON Schema; no additional fields.

# Example
Input:
```json
{
  "instruction": "Find a hotel under $100 with free parking, then save its address in notes.",
  "source_segments": [
    {
      "source_id": "S1",
      "text": "Find a hotel under $100 with free parking, then save its address in notes."
    }
  ]
}
```

Output:
```json
{
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
      "depends_on": [],
      "source_ids": [
        "S1"
      ]
    },
    {
      "goal_id": "G2",
      "goal_type": "ACT",
      "description": "Save the selected hotel address in notes.",
      "constraints": [],
      "completion_condition": "The selected hotel address is saved in notes.",
      "depends_on": [
        "G1"
      ],
      "source_ids": [
        "S1"
      ]
    }
  ],
  "issues": []
}
```
