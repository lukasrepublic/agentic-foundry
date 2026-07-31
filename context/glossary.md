# Glossary

- **Workspace** — this repo: the *WHAT* (SDLC artifacts + governance). **Factory** — the
  Foundry plugin: the *HOW* (verbs + gates), wired in.
- **Atom** — one capability-behavior; the unit of spec + authorization + merge.
- **Acceptance contract** — content-hashed, operator-signed observable checkpoints; the
  binding definition of done.
- **Live seam** — the runtime surface where a change is proven against the real running app
  (status ≠ functional). Exercised today by certification: `/foundry:certify-local` deploys the
  release once and runs the atoms' tagged journeys against it. (The bespoke "walk" engine that
  originally exercised this seam was retired.)
- **Front-authorization** — the operator signs *what* gets built before any implementation;
  no skip.
- (Add your project's domain terms here.)
