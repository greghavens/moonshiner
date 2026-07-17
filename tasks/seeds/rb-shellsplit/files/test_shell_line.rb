require "minitest/autorun"
require_relative "shell_line"

# Acceptance tests for ShellLine — the job-card runner's command-line
# splitter/quoter. Run: ruby test_shell_line.rb
class ShellLineTest < Minitest::Test
  def test_solves_it_here_rather_than_loading_the_stdlib_helper
    assert_nil defined?(Shellwords),
               "implement the pinned rules in shell_line.rb itself"
  end

  # -- split: plain words -----------------------------------------------------

  def test_plain_words_split_on_whitespace
    assert_equal %w[tar -czf backup.tgz docs],
                 ShellLine.split("tar -czf backup.tgz docs")
  end

  def test_runs_of_spaces_and_tabs_collapse
    assert_equal %w[a b c], ShellLine.split("  a\t\tb   c ")
  end

  def test_blank_lines_yield_no_words
    assert_equal [], ShellLine.split("")
    assert_equal [], ShellLine.split("   \t ")
  end

  # -- split: single quotes ---------------------------------------------------

  def test_single_quotes_keep_everything_literal
    assert_equal ["cp", "Monthly Report $Q3.txt", "archive/"],
                 ShellLine.split("cp 'Monthly Report $Q3.txt' archive/")
    assert_equal ["back\\slash kept"], ShellLine.split("'back\\slash kept'")
    assert_equal ["{ print $1 }"], ShellLine.split("'{ print $1 }'")
  end

  def test_newlines_inside_quotes_are_kept
    assert_equal ["line1\nline2"], ShellLine.split("'line1\nline2'")
  end

  def test_backslash_newline_inside_single_quotes_is_two_characters
    assert_equal ["a\\\nb"], ShellLine.split("'a\\\nb'")
  end

  # -- split: double quotes ---------------------------------------------------

  def test_double_quotes_group_but_escape_quote_dollar_backslash_backtick
    assert_equal ['say "when"'], ShellLine.split('"say \\"when\\""')
    assert_equal ["price: $5"], ShellLine.split('"price: \\$5"')
    assert_equal ["lit\\eral"], ShellLine.split('"lit\\\\eral"')
    assert_equal ["tick `here`"], ShellLine.split('"tick \\`here\\`"')
  end

  def test_backslash_before_anything_else_in_double_quotes_is_kept
    assert_equal ["back\\slash", "no\\tab"],
                 ShellLine.split('"back\\slash" "no\\tab"')
  end

  def test_backslash_newline_is_a_continuation_inside_double_quotes
    assert_equal ["onetwo"], ShellLine.split(%Q{"one\\\ntwo"})
  end

  # -- split: bare backslashes ------------------------------------------------

  def test_backslash_outside_quotes_escapes_anything
    assert_equal ["a b", "*", "it's"], ShellLine.split("a\\ b \\* it\\'s")
  end

  def test_backslash_newline_outside_quotes_is_a_continuation
    assert_equal %w[ls -l], ShellLine.split("ls \\\n-l")
  end

  # -- split: word assembly ---------------------------------------------------

  def test_adjacent_pieces_form_one_word
    assert_equal ["ab cd"], ShellLine.split("a'b c'd")
    assert_equal ["--name=Pat O'Brien"], ShellLine.split(%q{--name="Pat O'Brien"})
  end

  def test_empty_quotes_make_empty_words
    assert_equal ["run", "", ""], ShellLine.split(%q{run '' ""})
  end

  # -- split: malformed lines -------------------------------------------------

  def test_unterminated_quotes_raise
    assert_raises(ArgumentError) { ShellLine.split("echo 'oops") }
    assert_raises(ArgumentError) { ShellLine.split('echo "oops') }
  end

  def test_trailing_bare_backslash_raises
    assert_raises(ArgumentError) { ShellLine.split("echo oops\\") }
  end

  # -- escape -----------------------------------------------------------------

  def test_safe_words_pass_through_unchanged
    %w[rsync -avz --delete src/ user@host:/srv/media v1.2.3 a=b 85% x,y].each do |w|
      assert_equal w, ShellLine.escape(w)
    end
  end

  def test_empty_word_renders_as_empty_single_quotes
    assert_equal "''", ShellLine.escape("")
  end

  def test_unsafe_words_get_single_quotes
    assert_equal "'Monthly Report.txt'", ShellLine.escape("Monthly Report.txt")
    assert_equal "'*.bak'", ShellLine.escape("*.bak")
    assert_equal "'$HOME'", ShellLine.escape("$HOME")
    assert_equal "'a;b'", ShellLine.escape("a;b")
  end

  def test_embedded_single_quote_uses_the_close_escape_reopen_form
    assert_equal "'it'\\''s'", ShellLine.escape("it's")
    assert_equal "'O'\\''Brien, Pat'", ShellLine.escape("O'Brien, Pat")
  end

  # -- join -------------------------------------------------------------------

  def test_join_renders_a_copy_pasteable_line
    assert_equal "cp 'Monthly Report $Q3.txt' archive/",
                 ShellLine.join(["cp", "Monthly Report $Q3.txt", "archive/"])
  end

  def test_join_keeps_empty_words_visible
    assert_equal "run '' done", ShellLine.join(["run", "", "done"])
  end

  # -- the round-trip law -----------------------------------------------------

  def test_split_of_join_returns_the_original_argv
    [
      ["tar", "-cf", "Monthly Report Q3.tar", "./docs"],
      ["grep", "-e", "a b", "--file=notes rough.txt"],
      ["echo", "", "", "done"],
      ["printf", "%s\n", "O'Brien, Pat"],
      ["mv", "draft \"final\".txt", "archive/2026/"],
      ["awk", "{ print $1 }", "data.tsv"],
      ["sh", "-c", "cd /tmp && ls | wc -l"],
      ["note", "emoji ☕ and — dash"],
      ["weird", "back\\slash", "semi;colon", "tick`tock", "new\nline", "tab\there"]
    ].each do |argv|
      line = ShellLine.join(argv)
      assert_equal argv, ShellLine.split(line),
                   "round trip failed for #{line.inspect}"
    end
  end
end
