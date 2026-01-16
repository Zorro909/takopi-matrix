# Tests Alongside Implementation

Write tests as you implement, not as a separate phase after.

## Pattern

When implementing a feature or fix:
1. Understand the behavior to implement
2. Write tests for that behavior
3. Implement the code
4. Verify tests pass
5. Repeat for next behavior

## Why

- Catches issues immediately while context is fresh
- Ensures testable design (hard-to-test code gets fixed early)
- Provides safety net during implementation
- Avoids "I'll add tests later" (which often means never)

## DO
- Write test file alongside source file
- Test each public function/method
- Include edge cases (None, empty, invalid input)
- Run tests after each significant change

## DON'T
- Implement entire feature then "add tests"
- Skip tests for "simple" code
- Leave test writing for a separate PR

## Source Sessions
- 2026-01-14-takopi-matrix: 40 tests alongside implementation
- 2026-01-16: 46 tests added with code review fixes
