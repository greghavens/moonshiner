//! Builder-style command-line parser for our internal ops tools.
//!
//! Long flags only: boolean switches (`--verbose`), value options
//! (`--out FILE`, spelled `--out file` or `--out=file`), and declared
//! positional arguments filled in declaration order. Anything that starts
//! with a dash and was never declared is an error — an operator should not
//! have to wonder whether a typo'd flag was silently ignored.

use std::fmt;

/// Everything that can go wrong while parsing an argument vector.
///
/// `name` fields carry the flag token in its dashed form (`--jobs`) so the
/// message can be pasted straight back at the operator.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CliError {
    /// A dashed token that was never declared on this command.
    UnknownFlag { name: String },
    /// A value option appeared as the final token with nothing after it.
    MissingValue { name: String },
    /// A raw value could not be used where it appeared.
    InvalidValue {
        name: String,
        value: String,
        expected: &'static str,
    },
    /// More positional tokens than declared positionals.
    UnexpectedPositional { value: String },
    /// The argument vector ended before every declared positional was filled.
    MissingPositional { name: String },
}

impl fmt::Display for CliError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CliError::UnknownFlag { name } => write!(f, "unknown flag '{name}'"),
            CliError::MissingValue { name } => write!(f, "flag '{name}' expects a value"),
            CliError::InvalidValue {
                name,
                value,
                expected,
            } => write!(f, "invalid value '{value}' for '{name}': expected {expected}"),
            CliError::UnexpectedPositional { value } => {
                write!(f, "unexpected argument '{value}'")
            }
            CliError::MissingPositional { name } => {
                write!(f, "missing required argument <{name}>")
            }
        }
    }
}

impl std::error::Error for CliError {}

#[derive(Debug, Clone)]
enum FlagKind {
    Switch,
    Value { value_name: String },
}

#[derive(Debug, Clone)]
struct Flag {
    name: String,
    kind: FlagKind,
    help: String,
}

#[derive(Debug, Clone)]
struct Positional {
    name: String,
    help: String,
}

/// One command: a name plus its declared switches, options and positionals.
///
/// Built imperatively; declaration order is meaningful (positionals fill in
/// that order, and it is the display order for generated help).
#[derive(Debug, Clone)]
pub struct Command {
    name: String,
    about: String,
    flags: Vec<Flag>,
    positionals: Vec<Positional>,
}

impl Command {
    pub fn new(name: &str) -> Command {
        Command {
            name: name.to_string(),
            about: String::new(),
            flags: Vec::new(),
            positionals: Vec::new(),
        }
    }

    /// One-line description of the command.
    pub fn about(mut self, text: &str) -> Command {
        self.about = text.to_string();
        self
    }

    /// Declare a boolean switch: `switch("verbose", ..)` accepts `--verbose`.
    pub fn switch(mut self, name: &str, help: &str) -> Command {
        self.flags.push(Flag {
            name: name.to_string(),
            kind: FlagKind::Switch,
            help: help.to_string(),
        });
        self
    }

    /// Declare a value option: `option("out", "FILE", ..)` accepts
    /// `--out target/` or `--out=target/`.
    pub fn option(mut self, name: &str, value_name: &str, help: &str) -> Command {
        self.flags.push(Flag {
            name: name.to_string(),
            kind: FlagKind::Value {
                value_name: value_name.to_string(),
            },
            help: help.to_string(),
        });
        self
    }

    /// Declare the next required positional argument.
    pub fn positional(mut self, name: &str, help: &str) -> Command {
        self.positionals.push(Positional {
            name: name.to_string(),
            help: help.to_string(),
        });
        self
    }

    fn flag(&self, name: &str) -> Option<&Flag> {
        self.flags.iter().find(|f| f.name == name)
    }

    /// Parse an argument vector (program name already stripped off).
    pub fn parse(&self, args: &[&str]) -> Result<Matches, CliError> {
        let mut matches = Matches::new();
        let mut next_positional = 0;
        let mut i = 0;
        while i < args.len() {
            let token = args[i];
            i += 1;
            if let Some(body) = token.strip_prefix("--") {
                if body.is_empty() {
                    // A bare "--" is not a flag; nothing special is declared
                    // for it, so it goes through the positional path.
                    self.take_positional(&mut matches, &mut next_positional, token)?;
                    continue;
                }
                let (name, inline) = match body.split_once('=') {
                    Some((n, v)) => (n, Some(v)),
                    None => (body, None),
                };
                let dashed = format!("--{name}");
                let flag = self
                    .flag(name)
                    .ok_or_else(|| CliError::UnknownFlag {
                        name: dashed.clone(),
                    })?;
                match &flag.kind {
                    FlagKind::Switch => {
                        if let Some(v) = inline {
                            return Err(CliError::InvalidValue {
                                name: dashed,
                                value: v.to_string(),
                                expected: "no value",
                            });
                        }
                        matches.set_switch(name);
                    }
                    FlagKind::Value { .. } => {
                        let value = match inline {
                            Some(v) => v.to_string(),
                            None => {
                                if i >= args.len() {
                                    return Err(CliError::MissingValue { name: dashed });
                                }
                                let v = args[i].to_string();
                                i += 1;
                                v
                            }
                        };
                        matches.set_value(name, value);
                    }
                }
            } else if token.len() > 1 && token.starts_with('-') {
                // Short flags are not part of this parser; reject rather than
                // silently treating "-v" as a file name.
                return Err(CliError::UnknownFlag {
                    name: token.to_string(),
                });
            } else {
                self.take_positional(&mut matches, &mut next_positional, token)?;
            }
        }
        if next_positional < self.positionals.len() {
            return Err(CliError::MissingPositional {
                name: self.positionals[next_positional].name.clone(),
            });
        }
        Ok(matches)
    }

    fn take_positional(
        &self,
        matches: &mut Matches,
        next: &mut usize,
        token: &str,
    ) -> Result<(), CliError> {
        if *next >= self.positionals.len() {
            return Err(CliError::UnexpectedPositional {
                value: token.to_string(),
            });
        }
        matches
            .positionals
            .push((self.positionals[*next].name.clone(), token.to_string()));
        *next += 1;
        Ok(())
    }
}

/// The result of a successful parse. Getters take the bare declared name
/// (`"jobs"`, not `"--jobs"`).
#[derive(Debug, Default)]
pub struct Matches {
    switches: Vec<String>,
    values: Vec<(String, String)>,
    positionals: Vec<(String, String)>,
}

impl Matches {
    fn new() -> Matches {
        Matches::default()
    }

    fn set_switch(&mut self, name: &str) {
        if !self.switches.iter().any(|s| s == name) {
            self.switches.push(name.to_string());
        }
    }

    fn set_value(&mut self, name: &str, value: String) {
        if let Some(slot) = self.values.iter_mut().find(|(n, _)| n == name) {
            slot.1 = value; // repeated option: last occurrence wins
        } else {
            self.values.push((name.to_string(), value));
        }
    }

    /// Was `--name` given?
    pub fn switch(&self, name: &str) -> bool {
        self.switches.iter().any(|s| s == name)
    }

    /// Raw value of `--name`, if given.
    pub fn value(&self, name: &str) -> Option<&str> {
        self.values
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, v)| v.as_str())
    }

    /// Value of `--name` parsed as an integer; `Ok(None)` when absent.
    pub fn int(&self, name: &str) -> Result<Option<i64>, CliError> {
        match self.value(name) {
            None => Ok(None),
            Some(raw) => raw.parse::<i64>().map(Some).map_err(|_| CliError::InvalidValue {
                name: format!("--{name}"),
                value: raw.to_string(),
                expected: "an integer",
            }),
        }
    }

    /// Value of the declared positional `name`, if it was filled.
    pub fn positional(&self, name: &str) -> Option<&str> {
        self.positionals
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, v)| v.as_str())
    }
}
