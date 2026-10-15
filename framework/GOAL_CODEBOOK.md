# G2 — Local Goal Extraction Codebook

## Purpose and permitted evidence

Derive the user's requested Local Goals from the **original user instruction only**. Do not infer Goals from the agent's trajectory, visited pages, action history, hidden task evaluator, or the annotator's presumed execution plan. A Local Goal denotes a distinct **user-requested semantic outcome**, not a UI step.

## Goal types

**SEEK** — Find, identify, inspect, evaluate, compare, or select information, evidence, or candidate objects as a requested outcome.

**ACT** — Achieve a requested operation or application state: create, edit, configure, submit, upload, book, send, save, or open a specified object **when the opening/display is an independently requested outcome**. Do not classify routine information browsing as ACT merely because an action verb appears.

**MIXED** — One coherent requested outcome in which goal-relevant information retrieval, locating, or reading is meaningfully integrated with performing the operation, and the information and operation parts **cannot naturally form two separate user-requested outcomes**. A visual inspection, file open, or known-value lookup is not by itself sufficient for MIXED. A distinct choice among alternatives is not required when genuinely meaningful data locating/reading is integral.

## Segmentation decision

1. List the meaningful outcomes that the instruction **actually asks for**. Do not create a separate Goal for every click, scroll, navigation step, or tool.
2. If information finding and later execution each have an independently required semantic outcome, create separate SEEK and ACT Goals. Apply this even when one is a prerequisite of the other.
3. If information finding/reading and execution jointly constitute only one inseparable requested result, use MIXED when both functions are substantial. If reading is merely routine interface awareness during an operational procedure, ACT may be sufficient.
4. Multiple information requests become separate SEEK Goals when each has a meaningful independent result, not merely a separate visited page.
5. Assess Goal type from the task's intended result, not from the operations actually performed by a particular agent.

## Constraints, completion and dependencies

- Preserve task-specific names, file destinations, exclusions, quantities, priorities, quality standards and required order in `constraints`.
- `completion_condition` states the requested result in testable language, without claiming the recorded agent succeeded. Never weaken "upload" to "open upload screen" or "save" to "prepare a file".
- Use `depends_on` only for genuine data or semantic prerequisites. Express imposed chronological order in constraints when it is not a necessary data dependency.
- `issues` contains only ambiguities likely to alter Goal identity, required targets, constraints, or completion conditions. An ordinary MIXED Goal is not an issue.
- Goal-to-instruction references should use the application-supplied original-text segment identifiers (`source_ids`) when this field is supported by the approved schema. Do not copy large source quotes into the generated Goal.

## Deciding whether information is substantive

Use MIXED only when identifying, locating, organising or interpreting the task-relevant data constitutes a substantive part of the **single requested outcome**, and the information function cannot naturally be separated as its own requested result. Incidental reading of a known interface, selecting a known file, or checking a result does not turn ACT into MIXED. Assess this from the original instruction; do not infer MIXED from unforeseen difficulties in the recorded execution.

## Examples

### Integrated information work and execution — MIXED
**Task:** Calculate the sum of all columns in Excel.

**Goal:** MIXED — locate the relevant columns and calculate their sums.

**Why:** Determining the data to be aggregated and producing the sums may constitute one inseparable calculation outcome. Do not split a Goal solely to represent opening the spreadsheet or selecting cells.

### Operational work requiring visual reading — ACT
**Task:** Use GeoGebra to import an image of an axially symmetric vase, manually mark points along its outline, fit functions, calculate its volume, and save the result.

**Goal type:** ACT for the prescribed geometric modeling and file operations where each requested result merits its own Goal.

**Why:** Reading the image and choosing points can be intrinsic steps of the requested modeling operation; those steps do not by themselves create a separately requested information-finding outcome.

### Distinct information result followed by action — SEEK → ACT
**Task:** Find the paper entitled "OSWorld" on the specified website and upload it to HKU RIMS following the local guidelines.

**Goals:** SEEK — locate the requested paper; ACT — upload the identified paper according to the guidelines.

**Why:** Identifying the paper and uploading it are separately requested outcomes. Do not merge them into MIXED merely because the second uses the first.

### Identify requirements, then make appointments — SEEK → ACT
**Task:** Determine which required vaccines are missing from the saved vaccination record, then book the required appointments under location, date and price constraints.

**Goals:** SEEK — identify missing immunizations; ACT — schedule corresponding vaccinations.

**Why:** The list of missing vaccinations is an independently meaningful information result; booking is an external operation.

### Multiple pages are not automatically multiple Goals
**Task:** Use the Python website to inspect News and archived articles, and like a particular post.

**Goal decomposition:** Cluster information inspection by independently requested information results; distinguish the independently requested like action. Do not assign a new Goal just for entering the website, opening the News menu, or following an intermediate navigation link.

**Why:** UI navigation is not synonymous with an independently requested outcome.

### Search parameters and purchase flow
**Task:** Configure Google Maps journey criteria, compare public transport alternatives, then proceed through a ticket purchasing website nearly to the final purchase step.

**Goal decomposition:** Searching and comparing suitable routes is information work. Merely entering origin, date and transport filters does not make those clicks separate ACT Goals. Whether navigating through the requested ticket-order flow is an independently required ACT result depends on the explicit instruction and its completion condition; no completed payment should be inferred.

**Why:** Goal boundaries follow the requested information and operational outcomes, not whether a website uses forms and dropdowns.

### Preserve constraints and priorities
**Task:** Identify any missing vaccinations, and book appointments at clinics within 10 km, one vaccination per day. Choose the lowest price; break ties by shortest distance, then earliest available time.

**Goals:** SEEK — identify missing vaccines; ACT — make the relevant appointments.

**Constraints:** Every appointment must meet the distance and one-per-day rules; among feasible slots compare price first, then distance, then time.

**Why:** Losing the tie-break ordering changes the user's requested result. The exact 10 km bound and one-per-day limit are not optional background context.

### Completion criteria and prerequisites
**Task:** Find a named research paper on a specified website and upload it to the institution's research system following a local guideline document.

**Goal 1 (SEEK):** Identify the specified paper. **Completion:** The requested paper is located.

**Goal 2 (ACT):** Upload the identified paper according to the guidelines. **Completion:** The correct paper has been uploaded in the required manner. **depends_on:** Goal 1.

**Why:** Finding the paper is a data prerequisite of uploading that paper. Merely viewing the upload screen does not meet Goal 2's completion condition. A mentioned task order is not sufficient by itself to create a data dependency.

### Do not manufacture issues from ordinary missing background
**Task:** Calculate the sum of all columns in Excel.

**Goal:** MIXED when identifying the data columns and calculating their sums are substantively integrated.

**Why:** A lack of an explicitly named workbook does not automatically require an `issues` entry. Record an issue only if the missing information materially changes the target or expected result.
