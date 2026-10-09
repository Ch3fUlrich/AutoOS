R3 AUTH CLAUSE (auth/permissions/sessions/secrets/ACL):
REQUIRE an auth-regression test proving unauthenticated and wrong-role requests are still rejected BEFORE and AFTER the change (paste both runs); never weaken a check; all 4 review lenses must pass:
  - l3-review-diff (diff lens: reads the diff + PR text for local bugs and edge cases)
  - l3-review-tests (tests-run lens: runs the tests and the CI logs)
  - l3-review-codebase (codebase lens: touched files + callers + spec)
  - l3-review-transcript (transcript lens: full-session transcript review, II >= 44)
