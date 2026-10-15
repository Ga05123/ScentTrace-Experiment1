# G4 — Operator Annotation Codebook

## Unit, context, evidence

Label the **observable semantic function** of each existing native Unit relative to the task and the supplied Goal Alignment. Each Unit receives exactly one of INSPECT, COMMIT, LEAVE, EXECUTE, SUPPORT, UNRESOLVED, plus a brief `reason`. Preserve all original member actions and do not invent or split Units. An ACT Goal need not produce only EXECUTE Units; a SEEK Goal may include SUPPORT.

## INSPECT

Constructing, initiating, accessing, examining, checking, filtering, sorting or comparing **goal-relevant information**. A directly goal-oriented retrieval preparation or attempt may be INSPECT even if the current step retrieves no information or the search fails. Ordinary technical recovery is not automatically INSPECT.

**Example — search query:** To find seven-day weather information for Vancouver, the agent types "Vancouver, British Columbia" into the weather search. **INSPECT**, because the action constructs an information query rather than changing a user-requested task object.

**Example — relevant candidates:** To check rides at Six Flags Magic Mountain, the agent scrolls a park selector and reveals additional parks including Magic Mountain. **INSPECT**, because relevant candidates become available for comparison.

**Example — failed attempt:** When searching for pancake recipes, the agent submits a query and receives an empty-result page. **INSPECT**, because the attempted retrieval function is clear despite failure.

## COMMIT

Observable adoption of a **specific information candidate, answer or finding** as sufficient to resolve the current SEEK Goal or a clearly identifiable information need within a MIXED Goal. It is the agent's evidenced acceptance or use, not a guarantee of objective correctness or completion of the whole MIXED task. Assign the first Unit with sufficient adoption evidence; do not backdate from later results.

**Example — accepted recipe:** The agent's public response presents a specific pancake recipe as the requested answer. **COMMIT**, because an information result is being adopted and delivered.

**Example — answer-bearing terminal:** To judge whether a particular telephone number is associated with scams, the agent terminates with a substantive answer about that number. **COMMIT**, rather than classifying the entire Unit as a bare technical stop.

**Counterexample:** Opening a candidate detail page for further checking, entering a new query or writing down a still-unverified fact does not itself establish COMMIT.

## LEAVE

Observable abandonment of an information-search space **while the same information need remains unresolved**. A space can be a source, results area, or meaningful exploratory route. Full earlier branch history and the subsequent alternative route are not always necessary if abandonment is clear. A new URL, back navigation, query edit, different Goal, failure alone, or pure terminal alone does not suffice.

**Example — broken source:** While attempting to identify a movie from a Top 250 list available on AMC+, the agent encounters a broken Fandango route, abandons it and begins an independent Google search for the same unresolved movie information. **LEAVE**.

**Example — changing independent source:** During the same unresolved movie-information search, the agent abandons a Wikipedia verification route for an independent Bing search. **LEAVE**, where the prior exploratory space is visibly abandoned.

**Counterexample:** Refining terms within the same ongoing results exploration or returning from a movie's details to compare another listed candidate is ordinarily INSPECT, not automatic LEAVE.

## EXECUTE

Attempt or directly advance a specific user-requested operation on the task object or an application state: creating, editing, configuring, saving, sending, uploading, booking or carrying information with a known task destination. Goal-specific execution preparation can be EXECUTE; incidental application navigation and independent information queries need not be. Success is not required.

**Example — spreadsheet edit:** To sum Excel columns, the agent types a formula into a target spreadsheet cell. **EXECUTE**, because the task object is being modified even if the formula is imperfect.

**Example — information transfer:** After identifying a model's official URL for inclusion in specified Slides, the agent copies that URL as part of the transfer into the Slides document. **EXECUTE** may describe this directly goal-directed transfer step.

**Counterexample:** Entering "Richmond" into a DMV center search field is INSPECT even though it types into a form; no DMV visit has been booked or changed.

## SUPPORT

Technical preparation, waiting, maintenance and access recovery without a sufficiently evidenced information retrieval, adoption, branch exit or task-object execution function. Do not automatically classify all focus clicks, opening applications or menus as SUPPORT; check their goal-specific functional continuity. A bare terminate control is SUPPORT.

**Example — CAPTCHA:** An agent clicks a CAPTCHA tile while trying to access IMDb movie credits. **SUPPORT**, because the action primarily addresses an access barrier.

**Example — bare stop:** The agent emits only `computer.terminate()` without substantive answer or external operation. **SUPPORT**, even if the broader task was an ACT Goal.

## UNRESOLVED

The actual semantic function cannot be reliably distinguished from the permitted native action/observation evidence. Explain what is missing or which roles cannot be separated. A failed attempt with identifiable function keeps that function; missing post-state alone does not mechanically force UNRESOLVED.

**Example — genuinely ambiguous operation:** While editing a Power BI data model, an agent clicks within the application and the post-observation becomes a blank white view. The recorded action and surrounding observations do not identify the clicked target or whether the Unit selected a task object, changed the active view, or performed a recovery action. **UNRESOLVED** is appropriate only if the permitted adjacent context still cannot distinguish these roles. The missing information, not merely the absence of visible success, is the reason.

When the permitted neighboring observations reliably establish the operation, use that identifiable Operator instead.

## Compound Units and precedence

- Adopted information + clear inseparable external task operation: primary **EXECUTE**, explain the adopted information in `reason`.
- INSPECT + EXECUTE in the same indivisible Unit: determine the primary **evidenced semantic function**, no universal precedence.
- LEAVE + COMMIT: use the information need's observable unresolved/resolved state and the primary evidenced decision; if genuinely indeterminate, UNRESOLVED.
- Pure terminal: SUPPORT; answer-bearing terminal can COMMIT; external operation in a compound terminal can EXECUTE.

## Explanation requirements

INSPECT — identify the information activity. COMMIT — the adopted finding, relevant information need and acceptance evidence. LEAVE — the departed search space, still-unresolved need and abandonment evidence. EXECUTE — the task operation or task-specific execution step. SUPPORT — the auxiliary function. UNRESOLVED — the exact missing distinction. Never infer hidden model thought or eventual success.

## Distinguishing commonly confused Operators

### INSPECT versus SUPPORT
**Task:** Search for pancake recipes. **Actions:** Focusing the recipe query field and entering a goal-relevant query can be INSPECT as one continuous retrieval activity. Solving a CAPTCHA to gain access to an IMDb page is SUPPORT. **Reason:** The former advances information acquisition; the latter removes a technical obstacle.

**Technical-status distinction:** Querying process, window, installation or other system status primarily to diagnose or restore access to the task environment is ordinarily SUPPORT, even when the command returns information. INSPECT applies when the information itself can resolve the user's requested information need or a substantive task choice. If system diagnostics is itself the user-requested information outcome, the same query may instead be INSPECT. Classify by the information purpose, not by the presence of a query or terminal command.

**Example — technical process diagnosis (SUPPORT):** The user asks the agent to open a specified PDF in a document application. The agent types a command to check whether the application process is running as part of troubleshooting access. **SUPPORT**, because the intended information concerns environment recovery rather than the requested document operation. Do not claim the command was executed or returned a process list if the current Unit only typed it.

**Contrast — requested process information (INSPECT):** The user explicitly asks whether an application is running and requests its process details. The agent types the same type of process-query command. **INSPECT**, because obtaining the process information is the user-requested information outcome; if the current Unit only types the command, describe retrieval preparation rather than an observed result.

### INSPECT versus EXECUTE
**Task:** Find a staffed parcel-return location. **Action:** Submit a location search to obtain candidates — INSPECT. **Contrast:** Type a spreadsheet formula into a user-requested workbook — EXECUTE. **Reason:** An information-query interface is not a task-object change simply because it requires typing or clicking.

### INSPECT versus COMMIT
**Task:** Find a particular recipe. **Actions:** Opening a recipe to compare it with the criteria is INSPECT; adopting a specified recipe as the requested result in the substantive answer is COMMIT. **Reason:** Inspecting a possibility does not equal accepting it as sufficient.

### INSPECT versus LEAVE
**Task:** Identify an eligible film among a Top 250 list and streaming-service catalog. **Actions:** Continuing to inspect one source or refining the query is INSPECT; after a broken route, definitively abandoning it for an independent source for the same unresolved need can be LEAVE. **Reason:** LEAVE requires a distinguishable search space that was abandoned.

### COMMIT versus EXECUTE
**Task:** Find a model's official URL and add the link to specified slides. **Actions:** Accepting the URL as the selected result is COMMIT; carrying it into the designated slide is EXECUTE. **Reason:** Information adoption and task-object execution are separate functions. If both occur in one indivisible Unit with clear external execution, the Unit's primary label is EXECUTE.

### EXECUTE versus SUPPORT
**Task-specific application operations:** Directly configuring, installing, activating or launching the named task application as an evidenced step toward the user-requested operation on an identifiable task object can be EXECUTE, including when recovering from an earlier failure. Generic environment checks, maintenance, waiting and access-barrier handling without a clear task-operation function remain ordinarily SUPPORT. Do not label every installation, focus click or terminal command EXECUTE merely because it may later enable task execution. For an indivisible Unit combining recovery and operation, choose its primary evidenced semantic function; if the permitted evidence cannot distinguish them, explain the missing distinction under UNRESOLVED. Success is not required to recognize an identifiable execution attempt.

**Example — restarting with the specified document (EXECUTE):** The user asks to open a specified PDF in a document application. The agent types a compound command to stop a stalled instance and launch the application with the specified PDF; the following Unit submits the command. **EXECUTE** can apply to the task-specific command preparation and to its submission, provided the task-object operation is evidenced. Do not report that the PDF actually opened unless the resulting observation establishes it.

**Example — activating the task window (EXECUTE):** To continue the requested operation on a specified document, the agent types a command targeting an already identified application window for activation. **EXECUTE** can describe this direct, task-specific execution preparation. Typing alone is not evidence that the window has actually become active.

**Counterexample — enumerating windows to diagnose access (SUPPORT):** The agent types a command to list or filter application windows while troubleshooting access. **SUPPORT**, because the purpose is diagnosing environment state rather than performing the requested document operation. If the query has not yet been submitted, do not claim its results were observed.

### LEAVE versus SUPPORT
**Task:** Search film information across websites. **Actions:** Abandoning an unproductive information source with the need unresolved is LEAVE; dismissing an accidental login or solving an access-control barrier to resume the same search is SUPPORT. **Reason:** Technical recovery does not itself mean giving up a search space.

### UNRESOLVED versus identifiable failure
**Task:** Search for a constrained pancake recipe. **Actions:** Press Enter on the populated search form and receive no results — INSPECT, not UNRESOLVED. **Contrast:** A click within a Power BI screen followed by an uninformative blank state with no identifiable target or function may remain UNRESOLVED. **Reason:** An unsuccessful outcome is not evidence of an indeterminate Operator.

## Goal-incompatible behavior

If G3 is OFF_GOAL, still determine the observed G4 function. Under the agreed user-Goal-relative definitions, **do not assign COMMIT or LEAVE** in an OFF_GOAL Unit; do not create an unauthorized Goal to justify those labels. Clear information exploration may be INSPECT; clear external action can be EXECUTE; technical assistance may be SUPPORT. Where a substantive off-task answer has no reliable category under the six-class scheme, retain the case for direct adjudication instead of automatically labelling it UNRESOLVED.

## Avoid duplicating adoption at the terminal response

If an earlier Unit has already unambiguously adopted a specific information result (COMMIT), a later answer-bearing terminal does not automatically constitute a *new* information-selection decision. Examine the function of the answer-bearing Unit without backdating the initial adoption or counting the same selection twice. Do not mechanically count the same information selection twice. Classify the later Unit by its own substantive observable function, noting any unavoidable ambiguity in `reason`.
