# Runtime protocol draft

This draft describes a deliberately narrow runtime role: carry requests and results, record observable facts, and enforce hard permissions.

The runtime must not infer project quality, select a role or next action, require review or rework, declare completion, or convert a missing artifact into a task failure. Lead AI judgment remains outside the runtime.

Any future runtime contract should preserve this separation and avoid turning coordination metadata into a mandatory governance workflow.
