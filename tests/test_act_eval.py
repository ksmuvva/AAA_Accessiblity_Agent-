"""The ACT eval reads a rule file's criteria and examples, and scores the rules on them."""

import asyncio

from evals.act_eval import read_rule, run, score
from agent_access.models import Status

RULE = '''---
id: abc123
name: Image has an accessible name
accessibility_requirements:
  wcag20:1.1.1: # Non-Text Content (A)
    forConformance: true
  wcag20:1.4.6: # a secondary requirement
    secondary: This success criterion is more strict than this rule.
  wcag-technique:G94: # not a criterion
    forConformance: false
---

#### Passed Example 1

Has an alt.

```html
<html lang="en"><title>A</title><img src="cat.png" alt="A cat" /></html>
```

#### Failed Example 1

No alt.

```html
<html lang="en"><title>A</title><img src="cat.png" /></html>
```

#### Inapplicable Example 1

No HTML block in this one.
'''


def test_act_rules_are_read_and_scored(tmp_path):
    (tmp_path / "_rules").mkdir()
    (tmp_path / "_rules" / "image-abc123.md").write_text(RULE)
    rule, examples = read_rule(tmp_path / "_rules" / "image-abc123.md")
    assert rule["criteria"] == ["1.1.1"] and [e[:2] for e in examples] == [("passed", "Passed 1"), ("failed", "Failed 1")]
    assert score("failed", {Status.PASS}) == "false pass" and score("failed", {Status.NEEDS_REVIEW}) == "missed"
    assert score("failed", {Status.NOT_APPLICABLE}) == score("failed", {Status.PASS, Status.NOT_APPLICABLE}) == "false pass"
    assert score("passed", {Status.FAIL, Status.PASS}) == "false fail" and score("inapplicable", set()) == "ok"
    rows = asyncio.run(run(tmp_path, None))
    assert [(r["example"], r["result"]) for r in rows] == [("Passed 1", "ok"), ("Failed 1", "caught")]
