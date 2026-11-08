"""Small presentation rules for the personal task wall; never change source/status data."""
import re

EXERCISE = re.compile(r'übung|uebung|\b(?:UE|VU|LU|KU|PR)\b|\blab\b|exercise|praktikum',re.I)
LECTURE = re.compile(r'vorlesung|\bVO\b|\blecture\b',re.I)


def calendar_lecture(item):
    """Only positively identified lectures recede; ambiguous VU/lab sessions stay."""
    if item.get('pinned') or item.get('identity','').startswith('note:'):
        return False
    data = item.get('data',{})
    if data.get('kind')!='event':
        return False
    title = data.get('title') or ''
    course = item.get('course_title') or ''
    if EXERCISE.search(title):
        return False
    # An explicit lecture title is more specific than a combined course name.
    if LECTURE.search(title):
        return True
    if EXERCISE.search(course):
        return False
    return bool(LECTURE.search(course))
