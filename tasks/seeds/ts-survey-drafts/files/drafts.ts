export type Question = { id: string; label: string; required: boolean };
export type Section = { title: string; questions: Question[] };
export type Survey = {
  name: string;
  sections: Section[];
  meta: { locale: string; tags: string[] };
};

/** Start a new draft from a template. Drafts are meant to be edited freely. */
export function newDraft(template: Survey, name: string): Survey {
  return {
    ...template,
    name,
    meta: { ...template.meta },
  };
}

export function addQuestion(survey: Survey, sectionTitle: string, question: Question): void {
  const section = survey.sections.find((s) => s.title === sectionTitle);
  if (!section) {
    throw new Error(`no section titled ${JSON.stringify(sectionTitle)}`);
  }
  section.questions.push(question);
}

export function setRequired(survey: Survey, questionId: string, required: boolean): void {
  for (const section of survey.sections) {
    const question = section.questions.find((q) => q.id === questionId);
    if (question) {
      question.required = required;
      return;
    }
  }
  throw new Error(`no question with id ${JSON.stringify(questionId)}`);
}

export function tagSurvey(survey: Survey, label: string): void {
  if (!survey.meta.tags.includes(label)) {
    survey.meta.tags.push(label);
  }
}

export function questionCount(survey: Survey): number {
  return survey.sections.reduce((n, s) => n + s.questions.length, 0);
}
