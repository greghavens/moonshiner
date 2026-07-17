import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parse, plan, SqlError } from './sqlplan.ts';

// Parser + planner only: nothing here executes a query. ASTs and plans are
// plain objects compared with deepEqual — the trees ARE the contract.

const catalog = {
  users: { columns: ['id', 'name', 'age', 'city', 'active'] },
  orders: { columns: ['id', 'user_id', 'total'] },
};

// Every parse error carries a 1-based column, both as `col` on the error
// and at the front of the message: `col N: <expectation>, found <lexeme>`.
// `needle` locates the offending lexeme in the source; null means the
// error points one past the end of the input ("end of input").
function parseError(sql: string, needle: string | null, rest: string): void {
  const col = needle === null ? sql.length + 1 : sql.indexOf(needle) + 1;
  assert.throws(() => parse(sql), (e: unknown) => {
    assert.ok(e instanceof SqlError, `expected SqlError, got ${String(e)}`);
    assert.equal((e as SqlError).message, `col ${col}: ${rest}`);
    assert.equal((e as SqlError & { col: number }).col, col);
    return true;
  });
}

test('a bare SELECT * parses to the minimal AST', () => {
  assert.deepEqual(parse('SELECT * FROM users'), {
    columns: '*',
    table: 'users',
    where: null,
    orderBy: [],
    limit: null,
    offset: null,
  });
});

test('the full clause set parses to one exact tree, AND left-associative', () => {
  const ast = parse(
    "SELECT id, name FROM users WHERE age >= 21 AND city = 'Oslo' AND id IN (1, 2, 3) " +
    'ORDER BY name ASC, age DESC LIMIT 10 OFFSET 20',
  );
  assert.deepEqual(ast, {
    columns: ['id', 'name'],
    table: 'users',
    where: {
      op: 'and',
      left: {
        op: 'and',
        left: { op: '>=', column: 'age', value: 21 },
        right: { op: '=', column: 'city', value: 'Oslo' },
      },
      right: { op: 'in', column: 'id', values: [1, 2, 3] },
    },
    orderBy: [
      { column: 'name', dir: 'asc' },
      { column: 'age', dir: 'desc' },
    ],
    limit: 10,
    offset: 20,
  });
});

test('keywords are case-insensitive; identifiers keep their case', () => {
  const ast = parse('select Id from Users where Id != 7 order by Id limit 3');
  assert.deepEqual(ast.columns, ['Id']);
  assert.equal(ast.table, 'Users');
  assert.deepEqual(ast.where, { op: '!=', column: 'Id', value: 7 });
  assert.deepEqual(ast.orderBy, [{ column: 'Id', dir: 'asc' }]); // asc is the default
  assert.equal(ast.limit, 3);
  assert.equal(ast.offset, null); // no OFFSET clause
});

test('literals: quote-escaped strings, negative decimals, booleans, null', () => {
  const ast = parse(
    "SELECT id FROM users WHERE name = 'O''Hara' AND age > -3.5 AND active = true AND city != null AND active IN (false, true)",
  );
  const predicates: unknown[] = [];
  let node: any = ast.where;
  while (node && node.op === 'and') {
    predicates.unshift(node.right);
    node = node.left;
  }
  predicates.unshift(node);
  assert.deepEqual(predicates, [
    { op: '=', column: 'name', value: "O'Hara" },
    { op: '>', column: 'age', value: -3.5 },
    { op: '=', column: 'active', value: true },
    { op: '!=', column: 'city', value: null },
    { op: 'in', column: 'active', values: [false, true] },
  ]);
});

test('all six comparison operators parse', () => {
  for (const op of ['=', '!=', '<', '<=', '>', '>='] as const) {
    const ast = parse(`SELECT id FROM users WHERE age ${op} 30`);
    assert.deepEqual(ast.where, { op, column: 'age', value: 30 });
  }
});

test('the planner builds project(slice(sort(filter(scan)))) exactly', () => {
  const p = plan(
    'SELECT id, name FROM users WHERE age >= 21 AND active = true ' +
    'ORDER BY name DESC, id LIMIT 5 OFFSET 10',
    catalog,
  );
  assert.deepEqual(p, {
    node: 'project',
    columns: ['id', 'name'],
    input: {
      node: 'slice',
      offset: 10,
      limit: 5,
      input: {
        node: 'sort',
        keys: [
          { column: 'name', dir: 'desc' },
          { column: 'id', dir: 'asc' },
        ],
        input: {
          node: 'filter',
          predicates: [
            { op: '>=', column: 'age', value: 21 },
            { op: '=', column: 'active', value: true },
          ],
          input: { node: 'scan', table: 'users' },
        },
      },
    },
  });
});

test('clauses that are absent produce no plan node at all', () => {
  assert.deepEqual(plan('SELECT total FROM orders', catalog), {
    node: 'project',
    columns: ['total'],
    input: { node: 'scan', table: 'orders' },
  });
});

test('SELECT * expands to the catalog column order; filter flattens the AND chain in source order', () => {
  const p = plan(
    "SELECT * FROM users WHERE city != 'Oslo' AND id IN (1, 2) AND age < 65",
    catalog,
  ) as any;
  assert.deepEqual(p.columns, ['id', 'name', 'age', 'city', 'active']);
  assert.deepEqual(p.input, {
    node: 'filter',
    predicates: [
      { op: '!=', column: 'city', value: 'Oslo' },
      { op: 'in', column: 'id', values: [1, 2] },
      { op: '<', column: 'age', value: 65 },
    ],
    input: { node: 'scan', table: 'users' },
  });
});

test('LIMIT without OFFSET plans a slice with offset 0', () => {
  const p = plan('SELECT id FROM users LIMIT 25', catalog) as any;
  assert.deepEqual(p.input, {
    node: 'slice',
    offset: 0,
    limit: 25,
    input: { node: 'scan', table: 'users' },
  });
});

test('the planner rejects unknown tables and columns by name', () => {
  assert.throws(() => plan('SELECT id FROM userz', catalog), (e: unknown) =>
    e instanceof SqlError && (e as Error).message === 'unknown table "userz"');
  assert.throws(() => plan('SELECT nope FROM users', catalog), (e: unknown) =>
    e instanceof SqlError && (e as Error).message === 'unknown column "nope" in table "users"');
  assert.throws(() => plan('SELECT id FROM users WHERE agee > 1', catalog), (e: unknown) =>
    (e as Error).message === 'unknown column "agee" in table "users"');
  assert.throws(() => plan('SELECT id FROM users ORDER BY salery', catalog), (e: unknown) =>
    (e as Error).message === 'unknown column "salery" in table "users"');
  assert.throws(() => plan('SELECT id FROM orders WHERE name = 1', catalog), (e: unknown) =>
    (e as Error).message === 'unknown column "name" in table "orders"');
});

test('parse errors point at the offending token with what was expected', () => {
  parseError('UPDATE users', 'UPDATE', 'expected SELECT, found "UPDATE"');
  parseError('', null, 'expected SELECT, found end of input');
  parseError('SELECT FROM users', 'FROM', 'expected column name or *, found "FROM"');
  parseError('SELECT id users', 'users', 'expected FROM, found "users"');
  parseError('SELECT id FROM', null, 'expected table name, found end of input');
  parseError('SELECT id FROM users WHERE', null, 'expected column name, found end of input');
  parseError('SELECT id FROM users WHERE id =', null, 'expected a value, found end of input');
  parseError('SELECT id FROM users WHERE id 7', '7', 'expected a comparison operator or IN, found "7"');
  parseError('SELECT id FROM users ORDER name', 'name', 'expected BY, found "name"');
});

test('trailing input after a complete query is an error', () => {
  parseError('SELECT id FROM users 42', '42', 'expected end of input, found "42"');
  parseError('SELECT id FROM users LIMIT 5 WHERE id = 1', 'WHERE', 'expected end of input, found "WHERE"');
});

test('IN demands a parenthesised, non-empty value list', () => {
  parseError('SELECT id FROM users WHERE id IN 1, 2', '1', 'expected (, found "1"');
  parseError('SELECT id FROM users WHERE id IN ()', ')', 'expected a value, found ")"');
  parseError('SELECT id FROM users WHERE id IN (1, 2', null, 'expected ) or comma, found end of input');
});

test('LIMIT and OFFSET take non-negative integers only', () => {
  parseError('SELECT id FROM users LIMIT ten', 'ten', 'expected a non-negative integer, found "ten"');
  parseError('SELECT id FROM users LIMIT -5', '-5', 'expected a non-negative integer, found "-5"');
  parseError('SELECT id FROM users LIMIT 2.5', '2.5', 'expected a non-negative integer, found "2.5"');
  parseError('SELECT id FROM users LIMIT 5 OFFSET -1', '-1', 'expected a non-negative integer, found "-1"');
});

test('lexer errors: stray characters and unterminated strings', () => {
  parseError('SELECT id FROM users WHERE id ~ 3', '~', 'unexpected character "~"');
  parseError("SELECT id FROM users WHERE name = 'abc", "'abc", 'unterminated string');
});

test('parse and plan leave the catalog untouched and are pure functions of their input', () => {
  const snapshot = JSON.stringify(catalog);
  const sql = 'SELECT id FROM users WHERE age > 1 ORDER BY id LIMIT 1';
  assert.deepEqual(parse(sql), parse(sql));
  assert.deepEqual(plan(sql, catalog), plan(sql, catalog));
  assert.equal(JSON.stringify(catalog), snapshot);
});
