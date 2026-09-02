"""Quick routing verification for PHASE 5.6."""
from hermes.context.reference_resolver import ReferenceResolver
from hermes.context.conversational_context import ConversationalContext
from hermes.agent.goal_router import GoalRouter
from hermes.tools.manifest import ToolRegistry


def main() -> None:
    ctx = ConversationalContext(
        active_folder="Desktop/Hermes",
        last_created_folder="Desktop/Hermes",
    )
    resolver = ReferenceResolver()
    registry = ToolRegistry()
    router = GoalRouter(registry)

    cases = [
        (
            "create_folder Deneme2026",
            resolver.resolve(
                "Masaustune yeni bir klasor olustur, adina Deneme2026 koy.",
                ctx,
            ),
        ),
        (
            "open_app chrome",
            router.route("Chrome'u ac.", ctx),
        ),
        (
            "write_file deneme.txt",
            resolver.resolve("Hermes klasorunun icine deneme.txt", ctx),
        ),
        (
            "write_file ikinci.txt",
            resolver.resolve("son olusturdugun klasorun icine ikinci.txt", ctx),
        ),
    ]

    for label, result in cases:
        intent = getattr(result, "intent", None)
        if intent is None:
            print(f"{label}: NO INTENT")
            continue
        print(f"{label}: {intent.request.name} {intent.request.arguments}")


if __name__ == "__main__":
    main()
