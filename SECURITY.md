# Security

This is an engineering preview for kernel development and evaluation. The Ubuntu host is an Azure-managed guest with its own kernel and Azure agent; ThoxInux runs inside QEMU TCG under a locked unprivileged account.

The template denies all inbound network traffic. Its public IPv4 provides explicit outbound connectivity for package, release and Azure-agent traffic. Management uses Azure Run Command and therefore requires appropriate Azure role permissions. Guest controller commands are bounded, noninteractive and sent through an owner-only Unix socket. They are not interpreted as host shell commands.

The release files and bundled launcher are verified by exact byte size and SHA-256. Hashes establish byte identity with the reviewed pins; they are not a substitute for reviewing a new upstream release. The host uses Secure Boot and vTPM. The nested QEMU guest has a separate unsigned OVMF boot environment.

Report a suspected vulnerability through the private security reporting interface on the published GitHub repository when enabled, or contact the repository owner privately through the account's established channel. Do not open a public issue containing credentials, exploit details against a live THOX deployment, personal data or sensitive logs. Private reporting availability is not claimed until it is enabled on GitHub.

For an existing deployment, keep Ubuntu security updates current, restrict Run Command permissions and inspect current guest health. A successful deployment extension is a point-in-time check. It does not establish continuing health, complete Linux userspace readiness or a security certification.
