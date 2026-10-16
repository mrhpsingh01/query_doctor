from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Project, ProjectMember
from app.schemas.project import ProjectCreate, ProjectOut
from app.security.auth import require_project

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(request: Request, session: Session = Depends(get_session)):
    stmt = select(Project).order_by(Project.id)
    if request.app.state.settings.auth_tokens:
        stmt = stmt.join(ProjectMember).where(ProjectMember.subject == request.state.subject)
    return session.scalars(stmt).all()


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, request: Request, session: Session = Depends(get_session)):
    project = Project(name=body.name, description=body.description)
    session.add(project)
    session.flush()
    if request.app.state.settings.auth_tokens:
        session.add(ProjectMember(project_id=project.id, subject=request.state.subject, role="owner"))
    session.commit()
    return project


class MemberCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)


@router.post("/{project_id}/members", status_code=201)
def add_member(project_id: int, body: MemberCreate, request: Request, session: Session = Depends(get_session)):
    if not request.app.state.settings.auth_tokens:
        raise HTTPException(404, detail="project or subject not found")
    require_project(session, request, project_id)
    owner = session.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.subject == request.state.subject))
    if owner is None or owner.role != "owner" or body.subject not in request.app.state.settings.auth_tokens:
        raise HTTPException(404, detail="project or subject not found")
    existing = session.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.subject == body.subject))
    if existing is None:
        session.add(ProjectMember(project_id=project_id, subject=body.subject, role="member"))
        session.commit()
    return {"subject": body.subject, "role": existing.role if existing else "member"}
