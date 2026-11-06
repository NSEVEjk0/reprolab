# ReproLab entry preparation

## Problem, solution and useful distinction

Generated repairs need evidence beyond a success banner. ReproLab reproduces a real fixture bug, asks Vultr Serverless Inference for a repair, executes it in a fresh gVisor sandbox, checks independent cases and confirms removal. The evidence can be downloaded and replayed in another pinned sandbox.

Both the website and controller run on [Vultr](https://139.84.249.47.sslip.io). The completed host check passed a genuine repair, all 20 independent cases for that repair, fresh replay and cleanup. The OptimAI crawler was preserved. The three maintained Python fixtures are a focused first workflow; arbitrary repositories and languages are outside this version. Integrity hashes are not host attestations, and the checks are not a security audit.

## Required deliverables

- Public [repository](https://github.com/NSEVEjk0/reprolab) with setup, architecture, actual Vultr services and limitations in the README.
- Working public Vultr demo, with its access code provided separately to reviewers.
- Recorded **MP4** demo: [47.88-second narrated actual live repair](https://reprolab-ckay.vercel.app/demo/), with an independently checked sandbox policy and confirmed removal of both actual gVisor sandboxes. [Live evidence](presentation/reprolab-live-evidence.json); [recording fingerprint](presentation/recording.json).
- **PDF slides** covering the problem, workflow, architecture, evidence and limitations: [download the four-slide PDF](presentation/reprolab-slides.pdf). Its source is [presentation/slides.html](presentation/slides.html); `python3 scripts/export-slides.py` reproduces the PDF without downloads.
- Genuine human LabLab registration/submission. The published event phase ends 8 November; the precise submission-form cutoff has not been verified, so aim to submit on 7 November.

Sources: [Vultr challenge](https://lablab.ai/ai-hackathons/vultr-hackathon), [LabLab submission requirements](https://lablab.ai/hackathon-rules). Optional Vultr Community promotion is not needed for judging and is not being posted.

## Completed recording: 47.88 seconds

The recording used one genuine Vultr rounding repair on the connected deployment. The original failed 12 of 20 independent cases; the candidate passed all 20. The downloaded canonical evidence and hash agree. No candidate code ran locally, no fixture replaced live inference, and the recorder submitted no automatic second job. Exported frames and the measured MP4 duration were inspected before publication.

| Time | Show | Explain |
| --- | --- | --- |
| 0–7 s | Broken discount-rounding fixture on the Vultr site | A plausible repair can still be wrong. |
| 7–19 s | Actual live request and original/candidate check totals | Vultr inference proposed the repair; the independent evaluator accepted it. |
| 19–26 s | Actual sandbox policy evidence | gVisor, no network, a read-only root and nonroot execution. |
| 26–40 s | Exact candidate, evidence download and confirmed removal | The recorded baseline/candidate sandboxes were removed; unconfirmed cleanup cannot pass. |
| 40–48 s | Reproduction command and repository | The first scope is three Python fixtures. Integrity hashes are not host attestations. |

The recorded containment moment is lifecycle verification and confirmed sandbox removal, backed by the actual run's independent policy checks. Deliberate denied-network/timeout probes passed infrastructure verification, and a fresh replay passed the earlier operator check; those separate executions are not shown in this recording. No optional promotional video is needed.

`npm run test:browser-live` uses the declared Playwright package and an existing FFmpeg/libx264 installation to check and record the actual public workflow. It reads `/etc/reprolab/demo-access.txt` on the Vultr host or the existing private `.local/recording-access.txt` in this workspace; `REPROLAB_ACCESS_FILE` selects an explicit private file. It never reads the inference provider key. Each intentional recording run starts one live repair and counts toward the daily limit. The completed public artifacts are in `presentation/`; private raw recordings stay under `reports/submission/`.

## Announcement draft

ReproLab is live on Vultr: reproduce a Python bug, contain an AI repair and download evidence you can replay. Vultr Serverless Inference proposes the candidate; independent checks and a fresh gVisor sandbox decide whether it passes. The first lab covers scheduling, discount rounding and retried events. Try it: https://139.84.249.47.sslip.io · Code: https://github.com/NSEVEjk0/reprolab

Rewrite the announcement in your own voice. Demo access is supplied privately. This draft has not been posted.

## Submission outline

Describe the concrete bug, why independent acceptance matters, actual Vultr hosting/inference, gVisor lifecycle and budgets, live versus reference evidence, replay instructions, present limitations and the video/PDF URLs. Do not claim support for arbitrary repositories or a security audit.
