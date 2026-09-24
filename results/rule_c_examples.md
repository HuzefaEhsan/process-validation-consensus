# Rule (c) Examples (Audit)

Three problems that pass rules (a)+(b) but not rule (c), shown in full with the *terse*-tier log (one calculator step per annotation) and the gates it fails. Index 489 is shown as it was wired before the negative-literal fix (audit D8) and after it.

## GSM8K Test Index 265 — arithmetic continued in prose without an annotation

**Question.** Mrs. Cruz is looking for a house that will not go beyond her $400 000 budget. She saw a property that has a selling price of $350 000. On top of that, the buyer has to pay a brokerage fee which is 5% of the selling price, and also the transfer fee that is 12% of the selling price. How much more is the total price of the house than Mrs. Cruz's budget?

**Reference solution.**

```
The brokerage fee is $350 000 x 5/100 = $<<350000*5/100=17500>>17500.
The transfer fee is $350 000 x 12/100 = $<<350000*12/100=42000>>42000.
The total price of the house is $350 000 + $17500 + $42000 = $409 500.
So, it is $409 500 - $400 000 = $<<409500-400000=9500>>9500 more than Mrs. Cruz's budget.
#### 9500
```

**Terse log, as converted.** Dependency wiring [[], [], []].

| Step | Type | Depends on | Produces | Op | Content |
| ---: | --- | --- | --- | --- | --- |
| 0 | tool_call | [] | 17500 | op-0 | Compute 350000*5/100. |
| 1 | tool_call | [] | 42000 | op-1 | Compute 350000*12/100. |
| 2 | tool_call | [] | 9500 | op-2 | Compute 409500-400000. |

| Op | Tool | Input | Output | Deterministic |
| --- | --- | --- | --- | --- |
| op-0 | calculator | `350000*5/100` | "17500" | True |
| op-1 | calculator | `350000*12/100` | "42000" | True |
| op-2 | calculator | `409500-400000` | "9500" | True |

Final output: 9500 (from step 2)

Gates: process coherence False (orphan steps [0, 1]), tool utilization False, causal sufficiency True (C1 True, C2 True, C3 True, C4 True); decision **reject**.

## GSM8K Test Index 52 — an identity annotation stands in for a computation done in prose

**Question.** Uriah's book bag is getting too heavy for him. He needs to remove 15 pounds from it. His comic books weigh 1/4 pound each and his toys weigh 1/2 pound each. If he removes 30 comic books, how many toys does he need to remove?

**Reference solution.**

```
30 comic books weigh 7.5 pounds because 30 x .25 = <<30*.25=7.5>>7.5
He needs to remove 7.5 more pounds because 15 - 7.5 = <<15-7.5=7.5>>7.5
He needs to remove 15 toys because 7.5 / .5 = <<15=15>>15
#### 15
```

**Terse log, as converted.** Dependency wiring [[], [0], []].

| Step | Type | Depends on | Produces | Op | Content |
| ---: | --- | --- | --- | --- | --- |
| 0 | tool_call | [] | 7.5 | op-0 | Compute 30*.25. |
| 1 | tool_call | [0] | 7.5 | op-1 | Compute 15-7.5. |
| 2 | tool_call | [] | 15 | op-2 | Compute 15. |

| Op | Tool | Input | Output | Deterministic |
| --- | --- | --- | --- | --- |
| op-0 | calculator | `30*.25` | "7.5" | True |
| op-1 | calculator | `15-7.5` | "7.5" | True |
| op-2 | calculator | `15` | "15" | True |

Final output: 15 (from step 2)

Gates: process coherence False (orphan steps [1]), tool utilization False, causal sufficiency True (C1 True, C2 True, C3 True, C4 True); decision **reject**.

## GSM8K Test Index 489 — converter defect: a negative intermediate result was not wired (fixed, D8)

**Question.** The highest temperature ever recorded in Southlandia is -48 degrees Fahrenheit. The highest temperature ever recorded in Northlandia is 21 degrees Fahrenheit. The highest temperature recorded in Midlandia is -3 degrees Fahrenheit. What is the average highest temperature of these 3 countries?

**Reference solution.**

```
-48 + 21 + (-3) = <<-48+21+(-3)=-30>>-30
-30/3 = <<-30/3=-10>>-10 degrees
The average highest temperature recorded in Southlandia, Northlandia, and Midlandia is -10 degrees Fahrenheit.
#### -10
```

**Terse log, before the fix (deps [[], []]).** Dependency wiring [[], []].

| Step | Type | Depends on | Produces | Op | Content |
| ---: | --- | --- | --- | --- | --- |
| 0 | tool_call | [] | -30 | op-0 | Compute -48+21+(-3). |
| 1 | tool_call | [] | -10 | op-1 | Compute -30/3. |

| Op | Tool | Input | Output | Deterministic |
| --- | --- | --- | --- | --- |
| op-0 | calculator | `-48+21+(-3)` | "-30" | True |
| op-1 | calculator | `-30/3` | "-10" | True |

Final output: -10 (from step 1)

Gates: process coherence False (orphan steps [0]), tool utilization False, causal sufficiency True (C1 True, C2 True, C3 True, C4 True); decision **reject**.

**Terse log, after the fix.** Dependency wiring [[], [0]].

| Step | Type | Depends on | Produces | Op | Content |
| ---: | --- | --- | --- | --- | --- |
| 0 | tool_call | [] | -30 | op-0 | Compute -48+21+(-3). |
| 1 | tool_call | [0] | -10 | op-1 | Compute -30/3. |

| Op | Tool | Input | Output | Deterministic |
| --- | --- | --- | --- | --- |
| op-0 | calculator | `-48+21+(-3)` | "-30" | True |
| op-1 | calculator | `-30/3` | "-10" | True |

Final output: -10 (from step 1)

Gates: process coherence True (orphan steps []), tool utilization True, causal sufficiency True (C1 True, C2 True, C3 True, C4 True); decision **accept**.
