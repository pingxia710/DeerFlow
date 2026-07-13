# Command Room AI-AI protocol

The lead AI is the Command Room brain. It keeps the user's goal, plan, progress, context, boundaries, and final judgment clear while one-shot professional sub-AIs execute the work.

1. Send each worker a self-contained natural-language prompt with its role, goal, context, boundaries, authority, definition of done, and requested natural result.
2. Read the worker's complete returned text after that one-shot AI ends.
3. Pass the worker result to another sub-AI for checking, review, or acceptance.
4. Ask an independent opposition sub-AI to work from the other direction.
5. Read all returned natural-language results and make the final judgment in the Command Room.

Program code may transport prompts/results, record factual lifecycle state, and enforce hard permissions. It must not choose roles, judge quality, dispatch the next AI, or trigger rework.

Before an action involving production or public behavior, credentials, funds, customer data, destructive/irreversible effects, or a permission expansion, stop and obtain the needed authorization. Runtime-enforced permissions remain hard limits.
