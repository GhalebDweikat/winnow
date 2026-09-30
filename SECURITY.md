# Security

## Reporting a vulnerability

Please report it privately rather than in a public issue: open the
[Security tab](https://github.com/GhalebDweikat/winnow/security) and choose
**Report a vulnerability**. That creates a private advisory only the maintainer can see.

A useful report says which component is affected, how to reproduce it, and what an attacker
gains. A proof-of-concept request or payload is ideal.

winnow has one maintainer. Expect an acknowledgement within a few days and a fix, or a clear
explanation of why something is out of scope, as soon after that as the problem allows. Fixes
ship in the next release and the advisory names the reporter unless you'd rather it didn't.

## Supported versions

Only the latest release. winnow is pre-1.0 and fixes are not backported.

## Threat model

Knowing what winnow defends against saves everyone time, so here it is plainly.

**In scope**

- **The resident sidecar** (`winnow serve`, `127.0.0.1:47311`). It must not act on requests a
  web page can make: it rejects anything carrying an `Origin` header and anything that is not
  `Content-Type: application/json`. A way around either is a vulnerability.
- **Another local user on a shared machine** reaching the sidecar, reading `~/.winnow`, or
  spending your key.
- **The recall cache and decision log** in `~/.winnow` leaking what they hold.
- **Anything that makes winnow send data somewhere other than the judge and summarizer you
  configured**, or send more than the documented fields.
- **A crafted tool output or prompt** that makes winnow hide text it should have kept in a way
  that changes what Claude does, beyond the documented relevance threshold.

**Out of scope, by design**

- **Sending tool output to the judge.** That is what winnow does. What leaves your machine, and
  when, is listed in the README under
  [What leaves your machine](README.md#what-leaves-your-machine-and-what-it-costs). Set
  `WINNOW_JUDGE=off` to send nothing.
- **Code running as your own user.** It can already read `~/.winnow/env`, which holds your keys,
  so the sidecar does not try to defend against it.
- **A judge that is wrong.** A block hidden that turned out to be needed is a calibration
  problem, not a security one; the full text is cached and recoverable with `winnow_recall`.
  Report it as an ordinary issue.
