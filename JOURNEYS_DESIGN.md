# Design: audits planned by user journeys (step 4)

Status: proposal for review. Nothing here is implemented yet.

## Why

Today the unit of work is a WCAG criterion on a page. That finds defects on a page but misses barriers that only show
up when a person tries to get something done: a checkout that a keyboard user can't finish, a sign-up form whose
error a screen-reader user never hears, a menu that disappears at 400% zoom. Journeys make the task the unit of work
and map what goes wrong back to WCAG criteria afterwards.

## What changes, in one paragraph

The planner's `set_goal` gains `journeys`: a list of `{persona, task, start_page}`, for example
`{"persona": "keyboard", "task": "add an item to the basket and reach checkout", "start_page": "/"}`. A new planner
tool, `walk_journey(persona, task, start_page)`, runs a **journey walker** agent for that persona. The walker tries to
complete the task, stops at the first blocker or when done, and returns a transcript and a list of barriers. Each
barrier is recorded as a finding on the criterion it breaks, with the journey cited as evidence. The reviewer checks
those findings like any other agent finding.

## Personas (first version)

The persona is enforced by the **tools the walker gets**, not by its prompt, so it can't cheat by clicking.

| Persona | Tools | What it checks on the way |
|---|---|---|
| Keyboard only | `press_key` (Tab, Shift+Tab, Enter, Space, arrows, Escape), `inspect_element`, `take_screenshot`, `evaluate_js` (read-only). No `click_element`. | Can every step be reached and operated; is focus visible; is there a trap; does focus order make sense. |
| Screen reader | A new virtual cursor over the emulated reader (`sr_next(kind)`, `sr_prev(kind)` for heading, landmark, link, form field, any), `sr_activate`, `fill_field` on the focused field. No screenshots. NVDA via the bridge when it is available. | Does every control announce a name, role and state; are errors and status changes announced; can the task be done from what is announced alone. |
| 400% zoom | The keyboard and pointer tools, with the viewport held at 320 x 256 CSS px for the whole journey (the existing `run_probe` viewport option, kept on). | Does content reflow without two-way scrolling; is anything hidden, overlapped or cut off; do sticky headers cover focused content. |

Later candidates: reduced motion, high contrast or forced colours, voice control (label in name), and a cognitive
persona (timeouts, re-entering data). Each needs only a tool set and a short persona prompt.

## Barriers and how they map to WCAG

A walker returns, per step, what it did and what it observed, plus barriers:

```
Barrier {persona, task, step, observed, expected, selectors, evidence, severity: blocker | major | minor,
         criteria: ["2.1.1", ...]}
```

The walker maps each barrier to one or more criteria and records it with `record_finding` as a **fail or
needs_review, never a pass**, citing the journey (`journey J2, step 5`). A completed journey is not evidence that a
criterion passes; passes still come from judging the page. This keeps the coverage contract unchanged: journeys can
only add problems, never close criteria on their own.

## Verification

- The reviewer gets the journey transcript for any finding that cites one. It can't replay a whole journey, so it
  checks the cited step on the page where possible; otherwise the finding is kept as recorded and marked
  `not_reviewed`, the same rule the reviewer already applies to process states.
- `evaluate_js` scripts used during a journey are logged and cited as in step 3.

## Budget and safety

- Journeys cost model calls, so the planner chooses which to walk; each journey has a step cap (proposed: 40 actions).
- The existing safeguards apply unchanged: `network_guard` blocks writes, the destructive-intent guardrail refuses
  irreversible actions, and walkers use obviously fake data.

## Report

A new "Journeys" section: persona, task, outcome (completed, or blocked at step N), and each barrier with its
criteria and evidence. Barriers also appear under their criteria as usual.

## Proposed build order

1. Data model (`Journey`, `Barrier`), `walk_journey` tool, the keyboard-only walker, report section.
2. Screen-reader walker on the emulated reader (the new virtual-cursor tools), then NVDA through the bridge.
3. 400% zoom walker.
4. Eval cases with journey-only defects (a `div` button that can't be reached by keyboard; an error message with no
   live region; a menu that disappears at 320 px), so recall on those can be measured before and after.

## Decisions needed

1. Which persona first. Keyboard only is the simplest to enforce and to test.
2. Default or opt-in. Proposed: opt-in with `--journeys` until the evals show it finds more than it costs.
3. Who names the journeys: the user (`--journey "keyboard: reach checkout"`), the planner from the site's pages, or
   both. Proposed: both, with user journeys always walked.
4. Whether a walker may record a pass in a narrow case (for example, "the whole checkout is keyboard-operable" as
   evidence for 2.1.1). Proposed: no, to keep journeys from weakening the coverage contract.
