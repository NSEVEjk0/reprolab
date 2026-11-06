# ReproLab entry preparation

## Problem, solution and useful distinction

Generated repairs need evidence beyond a success banner. ReproLab reproduces a real fixture bug, asks Vultr Serverless Inference for a repair, executes it in a fresh gVisor sandbox, checks independent cases and confirms removal. The evidence can be downloaded and replayed in another pinned sandbox.

Both the website and controller run on [Vultr](https://139.84.249.47.sslip.io). The completed host check passed a genuine repair, all 20 independent cases for that repair, fresh replay and cleanup. The OptimAI crawler was preserved. The three maintained Python fixtures are a focused first workflow; arbitrary repositories and languages are outside this version. Integrity hashes are not host attestations, and the checks are not a security audit.

## Required deliverables

- Public [repository](https://github.com/NSEVEjk0/reprolab) with setup, architecture, actual Vultr services and limitations in the README.
- Working public Vultr demo, with its access code provided separately to reviewers.
- Recorded **MP4 or MOV** demo showing one containment moment.
- **PDF slides** covering the problem, workflow, architecture, evidence and limitations: [download the four-slide PDF](presentation/reprolab-slides.pdf). Its source is [presentation/slides.html](presentation/slides.html); `python3 scripts/export-slides.py` reproduces the PDF without downloads.
- Genuine human LabLab registration/submission. The published event phase ends 8 November; the precise submission-form cutoff has not been verified, so aim to submit on 7 November.

Sources: [Vultr challenge](https://lablab.ai/ai-hackathons/vultr-hackathon), [LabLab submission requirements](https://lablab.ai/hackathon-rules). Optional Vultr Community promotion is not needed for judging and is not being posted.

## Required recording: 55 seconds maximum

Record one genuine Vultr repair. Use the already connected deployment; the browser never needs the inference provider key. Do not replace the live run with a reference patch.

| Time | Show | Explain |
| --- | --- | --- |
| 0–7 s | Broken discount-rounding fixture on the Vultr site | A plausible repair can still be wrong. |
| 7–20 s | Actual live repair request and original failing checks | The controller reproduces the bug before asking Vultr for a repair. |
| 20–33 s | Passing candidate checks and containment evidence | Candidate execution has no network, a read-only root and fixed resource limits in gVisor. Show the real denied-network or timeout probe using dummy data. |
| 33–45 s | Confirmed teardown, exact candidate and evidence download | Success requires independent answers and confirmed cleanup. |
| 45–55 s | Fresh replay result and repository | The first scope is three Python fixtures. Evidence integrity is not hardware attestation. |

Prepare a real containment probe and fresh replay before recording. Edit waiting time only; keep the actual failure, result and cleanup visible. Keep the final video under 60 seconds. No optional promotional video is needed.

The prepared `scripts/record-demo.mjs` runs on the ordinary Vultr host with Playwright and system FFmpeg available. From the repository, run `REPROLAB_ROOT=/opt/reprolab node scripts/record-demo.mjs`. It reads the existing private demo access file, starts one real repair, records its actual containment-policy/cleanup evidence and exports a captioned MP4 capped at 55 seconds plus the evidence. It edits idle waiting and sends no automatic second job. Its execution is pending because the coding session blocks Chromium; do not claim the video has been produced until that command succeeds. The extra deliberate probe/replay shots can be recorded separately and trimmed into the same one-minute limit if needed.

## Announcement draft

ReproLab is live on Vultr: reproduce a Python bug, contain an AI repair and download evidence you can replay. Vultr Serverless Inference proposes the candidate; independent checks and a fresh gVisor sandbox decide whether it passes. The first lab covers scheduling, discount rounding and retried events. Try it: https://139.84.249.47.sslip.io · Code: https://github.com/NSEVEjk0/reprolab

Rewrite the announcement in your own voice. Demo access is supplied privately. This draft has not been posted.

## Submission outline

Describe the concrete bug, why independent acceptance matters, actual Vultr hosting/inference, gVisor lifecycle and budgets, live versus reference evidence, replay instructions, present limitations and the video/PDF URLs. Do not claim support for arbitrary repositories or a security audit.
