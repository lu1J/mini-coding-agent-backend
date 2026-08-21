from learning.day16_langchain import (
    ConceptCard,
    build_offline_runnable,
    multiply,
    text_length,
)


def test_day16_runnable_invoke():
    chain = build_offline_runnable()

    result = chain.invoke(
        "  hello agent  "
    )

    assert result == {
        "normalized": "hello agent",
        "uppercase": "HELLO AGENT",
        "length": 11,
    }


def test_day16_runnable_batch():
    chain = build_offline_runnable()

    result = chain.batch(
        [
            "  a  ",
            "  abc  ",
        ]
    )

    assert result[0]["length"] == 1

    assert (
        result[1]["uppercase"]
        == "ABC"
    )


def test_day16_tools():
    assert multiply.invoke(
        {
            "a": 6,
            "b": 7,
        }
    ) == 42

    assert text_length.invoke(
        {
            "text": "Agent",
        }
    ) == 5


def test_day16_structured_schema():
    card = ConceptCard(
        name="Runnable",
        beginner_explanation=(
            "统一的可运行组件。"
        ),
        interview_answer=(
            "LangChain 的组合执行抽象。"
        ),
        keywords=[
            "invoke",
            "batch",
            "stream",
        ],
    )

    assert card.name == "Runnable"

    assert "invoke" in card.keywords