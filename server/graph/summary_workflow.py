import logging
from typing import TypedDict, Dict, Any, List

from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from openai import ContentFilterFinishReasonError, BadRequestError
from pydantic import BaseModel, Field

from services.meeting_service import load_meeting
from utils.config import get_llm

logger = logging.getLogger(__name__)


class StructuredResult(BaseModel):
    """구조화된 요약 결과 모델"""
    subject: str = Field(description="회의 주제")
    paragraphs: List[Dict[str, Any]] = Field(description="단락별 요약된 회의내용")
    next_steps: List[str] = Field(description="회의에서 도출된 다음 단계")


class NodeState(TypedDict):
    meeting_id: str
    paragraphs: List[Dict[str, Any]]
    subject: str
    summaries: List[str]
    next_steps: List[str]
    current_step: str


class SummaryNode:
    def __init__(
            self, session_id: str = None
    ):
        self.graph = self.setup_graph()  # 그래프 설정
        self.session_id = session_id  # langfuse 세션 ID
        self.llm = get_llm()

    def setup_graph(self):
        # 그래프 생성
        workflow = StateGraph(NodeState)

        workflow.add_node("create_paragraphs", create_paragraphs)
        workflow.add_node("organize_next_steps_and_subject", organize_next_steps_and_subject)

        workflow.set_entry_point("create_paragraphs")

        workflow.add_edge("create_paragraphs", "organize_next_steps_and_subject")
        workflow.add_edge("organize_next_steps_and_subject", END)

        # 그래프 컴파일
        return workflow.compile()

    def run(self, meeting_id: str) -> StructuredResult | None:
        """단일 리드 처리"""
        initial_state = {
            "meeting_id": meeting_id,
            "paragraphs": [],
            "next_steps": [],
            "current_step": "initialized"
        }

        try:
            result = self.graph.invoke(initial_state)
            subject = result.get("subject")
            paragraphs = result.get("paragraphs")
            next_steps = result.get("next_steps")
            return StructuredResult(
                subject=subject,
                paragraphs=paragraphs,
                next_steps=next_steps
            )
        except (ContentFilterFinishReasonError, BadRequestError, ValueError) as e:
            paragraphs = str(e)
            print(f"{str(e)}")
            return None

def create_paragraphs(state: NodeState) -> NodeState:
    """회의내용 단락 생성 함수"""
    state["current_step"] = "create_paragraphs"
    meeting_id = state["meeting_id"]
    meeting = load_meeting(meeting_id)
    prompt = f"""
    다음 회의록을 읽고, 문단을 생성해줘.
    {meeting.transcript}
    """
    messages = [
        SystemMessage(
            content=f"""
            당신의 정체성:
            - 당신은 회의내용을 읽고, 내용별 요약과 문단을 생성하는 전문가입니다.

            당신의 역할:
            회의의 문맥을 파악하고 회의내용을 구분할 수 있게끔 문단을 생성하여 회의요약과 주제를 리턴하세요.
            문단은 회의내용의 흐름과 맥락에 따라 구분되어야 하며, 각 문단은 명확한 주제와 요약을 포함해야 합니다.
            문단은 transcript의 개수와 다를 수 있으며, 동일한 회의내용이라면 한 문단에, 다른 회의내용이라면 문단을 구분합니다.
            문단의 시작과 끝을 구분할 수 있도록 start, end 시간을 포함하여 문단을 생성하세요.
            start, end 시간은 문단에 포함된 회의내용의 시작과 끝 시간을 의미하며, transcript의 start, end 시간을 참고하여 생성하세요.
            문단을 요약할 수 없다면, 해당 transcript는 문단에 포함하지 않고, 건너뜁니다.
            입력된 문단의 내용을 기반으로 객관적으로 답변해야 합니다. 불필요한 설명이나 문단에 없는 내용은 추가하지 마세요.
            return example:
            [
                {{
                    "subject": "기술협상 및 계획수립",
                    "start": 720,
                    "end": 6736,
                    "summary": "기술협상을 위해 투입되는 인원에 대해 계획이 필요하며 공유 요청함.\n 계획을 수립하는데 있어 필요한 정보가 있다면 언제든 연락달라고 함."
                }},
                {{
                    "subject": "UX 컨설팅과 일정수립의 필요성",
                    "start": 6740,
                    "end": 88231,
                    "summary": "UX 컨설팅과 일정수립의 필요성에 대해 논의함. \n UX 컨설팅을 위해 투입인력의 역할구분을 요청함.\n LLM을 사용할 수 없는 환경을 고려하여 대안을 제시함."
                }}
            ]
            """
        ),
        HumanMessage(content=prompt),
    ]

    class Paragraph(BaseModel):
        subject: str = Field(description="문단의 주제")
        start: int = Field(description="문단 시작 시간(밀리초)")
        end: int = Field(description="문단 끝 시간(밀리초)")
        summary: str = Field(description="문단 요약")

    class StructuredOutput(BaseModel):
        paragraphs: List[Paragraph] = Field(description="회의 문단 목록")

    creative_llm = get_llm(0)
    structured_lld = creative_llm.with_structured_output(StructuredOutput)
    response = structured_lld.invoke(messages)
    paragraphs = [p.model_dump() for p in response.paragraphs]

    state["paragraphs"] = paragraphs
    return state


def summarize_meeting(state: NodeState) -> NodeState:
    """회의내용 요약 함수"""
    state["current_step"] = "summarize_meeting"
    summaries = []
    paragraphs = state["paragraphs"]
    for idx, paragraph in enumerate(paragraphs):
        prompt = f"""
        다음 회의록을 읽고, 요약해줘.
        {paragraph}
        """
        messages = [
            SystemMessage(
                content=f"""
                당신의 정체성:
                - 당신은 단락별 회의내용을 요약하는 전문가입니다.
                - 요약된 단락요약은 최대 500자가 넘지 않게 작성하세요.

                당신의 역할:
                단락의 문맥을 파악하고 회의요약과 주제를 리턴하세요.
                단락을 요약할 수 없다면, 요약과 주제는 빈 문자열("")을 리턴합니다.
                입력된 단락의 내용을 기반으로 객관적으로 답변해야 합니다. 불필요한 설명이나 단락에 없는 내용은 추가하지 마세요.
                return example:
                {{
                    "subject": "기술협상 및 계획수립",
                    "summary": "기술협상을 위해 투입되는 인원에 대해 계획이 필요하며 공유 요청함.\n 계획을 수립하는데 있어 필요한 정보가 있다면 언제든 연락달라고 함."
                }}
                """
            ),
            HumanMessage(content=prompt),
        ]

        class StructuredOutput(BaseModel):
            subject: str
            summary: str

        creative_llm = get_llm(0)
        structured_lld = creative_llm.with_structured_output(StructuredOutput)
        response = structured_lld.invoke(messages)
        summaries.append({
            "subject": response.subject,
            "summary": response.summary
        })

    state["summaries"] = summaries
    return state


def organize_next_steps_and_subject(state: NodeState) -> StructuredResult:
    """
    다락을 분석하여 회의주제와 다음 할 일을 도출하는 함수
    """
    state["current_step"] = "organize_next_steps_and_subject"
    paragraphs = state["paragraphs"]
    prompt = f"""
    다음 회의요약을 읽고, 회의주제와 다음 할 일을 안내해줘.
    {paragraphs}
    """
    messages = [
        SystemMessage(
            content=f"""
            당신의 정체성:
            - 당신은 요약된 회의내용을 기반으로 회의 이후에 해야할 일을 안내합니다.

            당신의 역할:
            요약된 회의내용을 참고해 회의전체주제와 다음 할 일을 안내합니다.
            다음 할 일은 회의에서 도출된 구체적인 행동 아이템으로 작성되어야 합니다. 예를 들어, "1. 기술협상을 위해 투입되는 인원에 대해 계획수립."과 같이 작성되어야 합니다.
            한 회의에서 여러 개의 다음 할 일이 도출될 수 있으므로, 1. ..., 2. ...과 같이 번호를 붙여서 작성해야 합니다.
            다음 할 일을 추론할 수 없다면, 다음할 일은 빈 문자열("")을 리턴합니다.
            입력된 단락의 내용을 기반으로 객관적으로 답변해야 합니다.
            return example:
                "subject": "UX 컨설팅과 일정수립의 필요성",
                "next_steps": ["1. 기술협상을 위해 투입되는 인원에 대해 계획수립.", "2. 계획수립에 필요한 정보 공유."]
            """
        ),
        HumanMessage(content=prompt),
    ]
    class StructuredOutput(BaseModel):
        subject: str
        next_steps: List[str] = Field(description="다음 할 일 목록")

    creative_llm = get_llm(0)
    structured_lld = creative_llm.with_structured_output(StructuredOutput)
    response = structured_lld.invoke(messages)
    state["subject"] = response.subject
    state["next_steps"] = response.next_steps

    return StructuredResult(
        paragraphs=state["paragraphs"],
        subject=state["subject"],
        next_steps=state["next_steps"]
    )