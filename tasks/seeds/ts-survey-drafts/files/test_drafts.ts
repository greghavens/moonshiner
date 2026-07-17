import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  addQuestion,
  newDraft,
  questionCount,
  setRequired,
  tagSurvey,
} from './drafts.ts';
import type { Survey } from './drafts.ts';

function baseTemplate(): Survey {
  return {
    name: 'template',
    sections: [
      {
        title: 'Profile',
        questions: [{ id: 'q_name', label: 'Your name', required: true }],
      },
      { title: 'Feedback', questions: [] },
    ],
    meta: { locale: 'en-US', tags: ['standard'] },
  };
}

test('a draft starts as a copy of the template', () => {
  const template = baseTemplate();
  const draft = newDraft(template, 'Q3 churn survey');
  assert.equal(draft.name, 'Q3 churn survey');
  assert.equal(questionCount(draft), 1);
  assert.deepEqual(draft.meta.tags, ['standard']);
});

test('editing a draft leaves the template untouched', () => {
  const template = baseTemplate();
  const draft = newDraft(template, 'Onboarding v2');
  addQuestion(draft, 'Feedback', { id: 'q_nps', label: 'How likely...', required: false });
  setRequired(draft, 'q_name', false);
  tagSurvey(draft, 'experimental');
  assert.deepEqual(template, baseTemplate());
});

test('sibling drafts do not share edits', () => {
  const template = baseTemplate();
  const draftA = newDraft(template, 'Draft A');
  addQuestion(draftA, 'Feedback', { id: 'q_a', label: 'Only in A', required: false });
  tagSurvey(draftA, 'a-only');

  const draftB = newDraft(template, 'Draft B');
  assert.equal(questionCount(draftB), 1, 'a fresh draft inherited an edit');
  assert.deepEqual(draftB.meta.tags, ['standard']);

  addQuestion(draftB, 'Feedback', { id: 'q_b', label: 'Only in B', required: true });
  assert.equal(questionCount(draftA), 2, "draft A changed when draft B was edited");
  assert.equal(questionCount(draftB), 2);
});

test('required flags are per draft', () => {
  const template = baseTemplate();
  const draftA = newDraft(template, 'Draft A');
  const draftB = newDraft(template, 'Draft B');
  setRequired(draftA, 'q_name', false);
  const inB = draftB.sections[0].questions.find((q) => q.id === 'q_name');
  assert.equal(inB?.required, true, "toggling draft A's question changed draft B");
});
