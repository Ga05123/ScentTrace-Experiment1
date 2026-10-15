# G3 — Goal–Trajectory Alignment Codebook

## Purpose and evidence

Determine the **single primary user-defined Local Goal**, if any, served by the current **native Unit**. Examine the original user instruction, the saved Goal Catalog, the native action and permitted observations, and the bounded neighboring units. Do not revise G2 Goals while annotating G3. Do not infer Goal alignment from a G4 Operator label or from eventual task success.

## Alignment states

**ALIGNED** — The primary observable function serves one identifiable user Local Goal. This includes meaningful preparation, attempts, waiting, retries and technical recovery **when the service to that Goal is clear**. If a Unit incidentally benefits another Goal, assign only the primary Goal rather than duplicating alignment.

**SUPPORT_NO_GOAL** — An identifiable technical support or control activity that cannot be assigned to any particular Goal. Pure terminal controls without substantive answer or execution content belong here.

**OFF_GOAL** — There is positive evidence that an action has substantive purpose yet serves **none** of the user's listed Goals. Do not use merely because the search failed, navigation is indirect, or the agent made a mistake while still pursuing a Goal.

**UNRESOLVED** — Available evidence cannot reliably establish the primary Goal, including rare indivisible Units that substantively serve multiple user Goals with no defensible primary. Retain only supported candidate Goal IDs and explicitly describe what cannot be distinguished. Do not use merely because a tool action failed, several Goals are mentioned in the instruction, or an OFF_GOAL action is evident.

## Boundaries and output

- Place a new interval boundary where the observed activity starts serving a different requested Goal. Meeting a `completion_condition` may help identify the switch but never defines it mechanically.
- A change of website, tool, page, action class, or annotation window does not independently imply a Goal boundary.
- Goal-serving SUPPORT Units remain ALIGNED and can remain in the same continuous Goal interval.
- In the proposed output, every target Unit must belong to exactly one inclusive `[first,last]` span. Merge only adjacent spans with identical `goal_ids` **and** `status`; preserve A → B → A.
- Goal IDs: ALIGNED **exactly one**; OFF_GOAL and SUPPORT_NO_GOAL **empty**; UNRESOLVED may contain supported candidate IDs (zero, one or several) only when the principal attribution is genuinely indeterminate.
- A pure `terminate()` control is SUPPORT_NO_GOAL without implying previous Goal completion or LEAVE. A compound terminal Unit containing a substantive answer or task-object action is attributed according to that content; preserve the native Unit.
- For a native Unit with actions affecting two user Goals, decide by the **dominant observable semantic function**. Directly serving G1 while creating a prerequisite for G2 is usually ALIGNED(G1), not simultaneous membership. If evidence cannot establish a primary Goal, use UNRESOLVED and explain the competing supported Goal IDs; do not split native Units.

## Examples

### Goal-serving preparation stays ALIGNED
**Task:** Find pancake recipes containing wheat and excluding beetroot.

**Sequence:** The agent focuses the recipe query field, enters relevant search text, and inspects returned recipes.

**Alignment:** ALIGNED to the recipe-finding Goal throughout when the sequence is observable.

**Why:** A preparation action need not contain new information to remain associated with the Goal it serves. G4 SUPPORT, if applicable to an individual click, would not imply G3 SUPPORT_NO_GOAL.

### Pure task termination has no active Goal assignment
**Task:** Inspect a route in Google Maps and continue toward the ticket-purchase website.

**Sequence:** Route and ticket-related actions are followed by a Unit containing only `computer.terminate()`.

**Alignment:** The preceding actions retain their evidence-supported Goals. The pure terminal Unit is SUPPORT_NO_GOAL with an empty Goal set.

**Why:** A control-only stop is neither an additional user-requested outcome nor proof that the immediately preceding Goal succeeded.


### OFF_GOAL — substantive departure from the user's Goals
**Task:** Find top attractions and hidden gems in Spain.

**Sequence:** Instead of researching attractions, the agent navigates an airline site, opens Dublin-to-Madrid flight offers, starts the flight-search/booking flow, and receives flight-related error/results pages.

**Alignment:** OFF_GOAL for the evidenced flight-booking activity; none of the user's attraction-discovery Goals calls for purchasing or investigating flights.

**Why:** The observed actions have a substantive and identifiable purpose but do not serve any user-defined Goal. An unexpected site alone would not be enough: the decisive evidence is the extended flight-specific interaction.

### UNRESOLVED — uncertain Goal attribution
**Task:** Compare two pescatarian diets for healthier eating.

**Sequence:** After unsuccessful access to one diet article, the agent visits a different, Mediterranean-diet URL; the recorded page does not load. From the available navigation and failed page, it is unclear whether this is part of the requested diet comparison or a separate departure.

**Alignment:** UNRESOLVED when the allowed neighboring observations still cannot determine the new route's Goal relevance.

**Why:** An unsuccessful page load alone is insufficient. Here the ambiguity concerns the *goal relevance of the route*, not just the failure. If adjacent evidence resolves the relevance, use ALIGNED or OFF_GOAL instead.

### Primary Goal selection across neighboring activities
**Task:** Check a theme park’s ride details and then view photographs of a particular ride.

**Sequence:** The agent opens the ride-details page, scrolls to its photo gallery, and subsequently opens separate ride photographs.

**Alignment:** Each native Unit is ALIGNED to the specific Goal it principally serves, with an observable boundary as the activity shifts from ride details to photographs.

**Why:** Consecutive activities serving two different Goals are not evidence that each Unit simultaneously belongs to both. An activity that also helps later work remains aligned to its principal current Goal. If a genuinely inseparable Unit serves both and no primary can be reliably distinguished, choose UNRESOLVED with an explicit reason; do not fabricate a new Goal.
