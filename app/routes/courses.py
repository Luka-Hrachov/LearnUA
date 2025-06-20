from flask import Blueprint, request, jsonify, current_app, make_response
from flask_jwt_extended import jwt_required, current_user
from pydantic import ValidationError
import json
from ..schemas import CourseIn, SectionIn, LessonIn, StepIn, StepQuery
import pydantic
from ..models import Course, Section, Lesson, Step
from ..extensions import db
from sqlalchemy import select
import os
from urllib.parse import parse_qs
from ..utils import update_step_place


courses_bp = Blueprint('course', __name__)

@courses_bp.route('/', methods=['POST'])
@jwt_required()
def create_course():
    if not request.is_json:
        return jsonify(msg='Expected json format'), 415

    try:
        course_model = CourseIn.model_validate(request.get_json())
        course = Course(title=course_model.title,
                        description=course_model.description,
                        author_id=current_user.id
                )
        
        if Course.query.filter_by(author_id=current_user.id, title=course_model.title).all():
            return jsonify(msg='Course title must be unique'), 400

        db.session.add(course)
    except pydantic.ValidationError as e:
        return jsonify(
            msg=str(e),
            example_json=CourseIn.model_json_schema().get('examples')
        ), 400
    except Exception as e:
        db.session.rollback()
        return jsonify(msg=f'Server error, please report: {e}'), 500

    db.session.commit()

    return jsonify(msg='Course created successfully', course_id=course.id), 201

@courses_bp.route('/<int:course_id>/<int:section_place>/<int:lesson_place>', methods=['POST'])
@jwt_required()
def add_step(course_id, section_place, lesson_place):

    sub_query = select(Section.id).where(
        Section.course_id.in_(select(Course.id).where(Course.author_id == current_user.id).scalar_subquery()),
        Section.course_id == course_id,
        Section.place == section_place
    )
    lesson = db.session.execute(select(Lesson).where(
        Lesson.section_id.in_(sub_query),
        Lesson.place == lesson_place
    )).scalars().one_or_none()
    if not lesson:
        return jsonify(msg='Resource not found'), 400

    try:
        input_model = StepIn.model_validate(request.get_json())
        step_place = Step.query.filter_by(lesson_id=lesson.id).count() + 1
        path = os.path.join(os.path.split(current_app.root_path)[0], 'uploads', f'user_{current_user.id}',
                            f'course_{course_id}', f'section_{section_place}',
                            f'lesson_{lesson_place}')

        os.makedirs(path, exist_ok=True)
        full_path = os.path.join(path, f'step_{step_place}_{input_model.model.content_type}.json')
        step = Step(lesson_id=lesson.id, place=step_place,
                    content_type=input_model.model.content_type, content_path=full_path)
        db.session.add(step)
        db.session.flush()

        input_model.step_id = step.id
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(input_model.model_dump_json(indent=2))
    except pydantic.ValidationError as e:
        return jsonify(
            msg=str(e),
            example_json=StepIn.model_json_schema().get("examples")
        ), 400
    except Exception as e:
        db.session.rollback()
        return jsonify(msg=f'Server error, please report: {e}'), 500

    db.session.commit()
    return jsonify(msg='Step successfully created', step_id=step.id, content_path=full_path)


@courses_bp.route('/<int:course_id>/<int:section_place>/<int:lesson_place>')
@jwt_required()
def get_step(course_id, section_place, lesson_place):
    query_params = {k: v if len(v) > 1 else v[0] for k, v in
                    parse_qs(request.query_string.decode(encoding='utf-8')).items()}

    try:
        step_place = StepQuery.model_validate(query_params).step_place
        path = os.path.join(
            os.path.split(current_app.root_path)[0],
            "uploads",
            f"user_{current_user.id}",
            f"course_{course_id}",
            f"section_{section_place}",
            f"lesson_{lesson_place}",
        )
        filename = f'step_{step_place}_'

        if os.path.isdir(path):
            for f in os.listdir(path):
                if f.startswith(filename):
                    with open(os.path.join(path, f), 'r') as file:
                        step_data = json.load(file)
                        return jsonify(step_data=step_data)

    except ValidationError as e:
        return jsonify(msg=f"Wrong query input: {e}"), 400
    except Exception as e:
        return jsonify(msg=f'Server error, please report: {e}'), 500

    return jsonify(msg='Step not found'), 404


@courses_bp.route('/<int:course_id>/<int:section_place>/<int:lesson_place>', methods=['DELETE'])
@jwt_required()
def delete_step(course_id, section_place, lesson_place):
    query_params = {k: v if len(v) > 1 else v[0] for k, v in
                    parse_qs(request.query_string.decode(encoding='utf-8')).items()}

    try:
        step_place = StepQuery.model_validate(query_params).step_place
    except ValidationError:
        return jsonify(msg='Missing query parameter \'step_place\''), 400

    try:
        course = select(Course.id).where(Course.author_id == current_user.id,
                                    Course.id == course_id)
        section = select(Section.id).where(
            Section.course_id.in_(course), Section.place == section_place
        )
        lesson = select(Lesson.id).where(
            Lesson.section_id.in_(section), Lesson.place == lesson_place
        )
        step = db.session.execute(select(Step).where(
            Step.lesson_id.in_(lesson), Step.place == step_place
        )).scalar()

        if not step:
            return jsonify(msg='Step not found'), 400

        db.session.delete(step)
        db.session.flush()

        if os.path.isfile(step.content_path):
            os.remove(step.content_path)

        steps = Step.query.filter_by(lesson_id=step.lesson_id).order_by(Step.place).all()
        for p, st in enumerate(steps, start=1):
            st.place = p
            st.content_path = update_step_place(st.content_path, p)

        db.session.commit()
        return jsonify(msg=f'Successfully deleted step {step.id} from\n{step.content_path}'), 200
    except Exception as e:
        return jsonify(msg=f'Server error, please report: {e}'), 500

@courses_bp.route('/<int:course_id>')
def get_course_info(course_id):
    course = Course.query.filter_by(id=course_id).one_or_none()
    if not course:
        return jsonify(
            msg='Course not found'
        ), 404
    return jsonify(
        title=course.title,
        description=course.description,
        created_at=str(course.created_at),
        rating=course.rating,
        sections=[{'title': c.title, 'place': c.place, 'id': c.id, 'lessons': [
            {'title': les.title, 'place': les.place, 'id': les.id} for les in c.lessons
        ]}
                    for c in course.sections]
    )
















