from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any

class VisionSegment(BaseModel):
    question_id: str = Field(..., description="The ID of the question (e.g., Q1, Q2)")
    extracted_text: str = Field(..., description="The full handwritten answer text extracted for this question")

class VisionOutput(BaseModel):
    segments: List[VisionSegment] = Field(..., description="List of extracted answers segmented by question")

class ScorerOutput(BaseModel):
    score_fraction: float = Field(..., description="The score fraction awarded between 0.0 and 1.0 based on answer quality")
    reasoning: str = Field(..., description="2-3 sentence explanation of the score")
    missing_concepts: List[str] = Field(default_factory=list, description="List of key concepts missing from the answer")
    strengths: List[str] = Field(default_factory=list, description="List of strengths in the student's answer")

# --- Indian Exam Evaluation Schemas ---

class StepMark(BaseModel):
    step_description: str = Field(..., description="Description of the step or derivation requirement")
    max_marks: float = Field(..., description="Maximum marks for this step")
    marks_awarded: float = Field(default=0.0, description="Marks awarded for this step")
    feedback: Optional[str] = Field(default="", description="Examiner comment on this step")

class RubricQuestion(BaseModel):
    question_id: str = Field(..., description="Unique question identifier (e.g. Q1, Q1A, Q12)")
    section: Optional[str] = Field(default="General", description="Section name (e.g. Section A, Section B)")
    max_marks: float = Field(..., description="Total marks allocated to this question")
    step_marks: List[StepMark] = Field(default_factory=list, description="Step-marking breakdown")
    model_answer: str = Field(..., description="Canonical model answer or derivation")
    keywords: List[str] = Field(default_factory=list, description="Key concepts or keywords required")

class RubricSection(BaseModel):
    name: str = Field(..., description="Section name")
    max_marks: float = Field(..., description="Maximum marks for the section")
    attempt_any: Optional[int] = Field(default=None, description="Optional 'attempt any N' limit")

class IndianExamRubric(BaseModel):
    rubric_id: str = Field(..., description="Unique ID for this rubric")
    title: str = Field(..., description="Title of the examination paper")
    subject: str = Field(..., description="Academic subject")
    max_pages: int = Field(default=64, description="Maximum permitted pages in student booklet")
    sections: List[RubricSection] = Field(default_factory=list)
    questions: List[RubricQuestion] = Field(default_factory=list)

class QuestionEvaluationResult(BaseModel):
    question_id: str
    section: str = "General"
    attempted: bool = True
    marks_awarded: float = 0.0
    max_marks: float = 0.0
    step_evaluations: List[Dict[str, Any]] = Field(default_factory=list)
    failure_modes: List[str] = Field(default_factory=list)
    student_feedback: str = ""
    ocr_confidence: float = 1.0
    semantic_similarity: float = 0.0
    calibrated_confidence: float = 1.0
    requires_human_review: bool = False
    pages: List[int] = Field(default_factory=list)
    has_diagram: bool = False

class IndianExamEvaluationReport(BaseModel):
    job_id: str
    user_id: Optional[str] = None
    rubric_id: str
    status: str
    total_marks_awarded: float
    max_total_marks: float
    percentage: float
    section_breakdown: Dict[str, Any] = Field(default_factory=dict)
    questions: List[QuestionEvaluationResult] = Field(default_factory=list)
    failure_mode_summary: Dict[str, int] = Field(default_factory=dict)
    completed_at: Optional[str] = None
