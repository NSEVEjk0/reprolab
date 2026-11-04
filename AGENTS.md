# ReproLab

Generated source executes only through the fixed `Sandbox` gVisor runner. Keep expected answers outside candidate execution. Require the immutable image digest, runsc runtime, no network, read-only mounts, nonroot UID and fixed CPU/memory/PID/time/output limits. Never add a host-execution or runc fallback.

Live repairs require Vultr Serverless Inference. Explicit reference evaluations are for infrastructure tests and clearly labelled examples; never use them as live inference fallback. Keep provider keys out of containers, evidence, browser bundles and logs. Preserve bounded queue/admission and capability-scoped job reads.

Every verified result requires a reproduced baseline failure, valid output protocol, passing independent cases, recorded policy and confirmed teardown. Integrity hashes are not hardware or host attestations. Failed cleanup must remain visible; the owned-container watchdog covers controller restarts without touching other projects' containers.

Required checks: unit tests, real gVisor integration/probes on an isolated CI host, JavaScript syntax and the primary browser workflow. A static preview is not a Vultr-compliant deployment. Do not claim actual live inference or host deployment without evidence.
