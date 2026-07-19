//! Protected offline surface of the clap 4.5 APIs used by this fixture.

use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgAction {
    Set,
    SetTrue,
    Count,
}

#[derive(Debug, Clone)]
pub struct Arg {
    id: String,
    long: Option<String>,
    short: Option<char>,
    help: String,
    value_name: Option<String>,
    default: Option<String>,
    action: ArgAction,
    num_args: usize,
    required: bool,
    global: bool,
    conflicts: Vec<String>,
    removed_calls: Vec<&'static str>,
}

impl Arg {
    pub fn new(id: &str) -> Self {
        Self {
            id: id.to_string(),
            long: None,
            short: None,
            help: String::new(),
            value_name: None,
            default: None,
            action: ArgAction::Set,
            num_args: 0,
            required: false,
            global: false,
            conflicts: Vec::new(),
            removed_calls: Vec::new(),
        }
    }

    pub fn long(mut self, value: &str) -> Self {
        self.long = Some(value.to_string());
        self
    }

    pub fn short(mut self, value: char) -> Self {
        self.short = Some(value);
        self
    }

    pub fn help(mut self, value: &str) -> Self {
        self.help = value.to_string();
        self
    }

    pub fn value_name(mut self, value: &str) -> Self {
        self.value_name = Some(value.to_string());
        self
    }

    pub fn default_value(mut self, value: &str) -> Self {
        self.default = Some(value.to_string());
        self
    }

    pub fn num_args(mut self, value: usize) -> Self {
        self.num_args = value;
        self
    }

    pub fn action(mut self, value: ArgAction) -> Self {
        self.action = value;
        self
    }

    pub fn required(mut self, value: bool) -> Self {
        self.required = value;
        self
    }

    pub fn global(mut self, value: bool) -> Self {
        self.global = value;
        self
    }

    pub fn conflicts_with(mut self, id: &str) -> Self {
        self.conflicts.push(id.to_string());
        self
    }

    pub fn takes_value(mut self, value: bool) -> Self {
        self.removed_calls.push("Arg::takes_value");
        self.num_args = usize::from(value);
        if !value {
            self.action = ArgAction::SetTrue;
        }
        self
    }

    pub fn multiple_occurrences(mut self, value: bool) -> Self {
        self.removed_calls.push("Arg::multiple_occurrences");
        if value {
            self.action = ArgAction::Count;
        }
        self
    }

    fn option_label(&self) -> String {
        let mut names = match (self.short, &self.long) {
            (Some(short), Some(long)) => format!("-{short}, --{long}"),
            (None, Some(long)) => format!("    --{long}"),
            _ => self.value_name.clone().unwrap_or_else(|| self.id.to_uppercase()),
        };
        if self.num_args == 1 {
            names.push_str(&format!(
                " <{}>",
                self.value_name.clone().unwrap_or_else(|| self.id.to_uppercase())
            ));
        }
        names
    }
}

#[derive(Debug, Clone)]
pub struct Command {
    name: String,
    version: String,
    about: String,
    args: Vec<Arg>,
    subcommands: Vec<Command>,
    require_subcommand: bool,
}

impl Command {
    pub fn new(name: &str) -> Self {
        Self {
            name: name.to_string(),
            version: String::new(),
            about: String::new(),
            args: Vec::new(),
            subcommands: Vec::new(),
            require_subcommand: false,
        }
    }

    pub fn version(mut self, value: &str) -> Self {
        self.version = value.to_string();
        self
    }

    pub fn about(mut self, value: &str) -> Self {
        self.about = value.to_string();
        self
    }

    pub fn arg(mut self, value: Arg) -> Self {
        self.args.push(value);
        self
    }

    pub fn subcommand(mut self, value: Command) -> Self {
        self.subcommands.push(value);
        self
    }

    pub fn subcommand_required(mut self, value: bool) -> Self {
        self.require_subcommand = value;
        self
    }

    pub fn validate(&self) -> Result<(), Error> {
        for arg in &self.args {
            if let Some(call) = arg.removed_calls.first() {
                return Err(Error::configuration(format!(
                    "command {} uses removed {call} builder API",
                    self.name
                )));
            }
            if arg.action == ArgAction::Set && arg.num_args != 1 {
                return Err(Error::configuration(format!(
                    "value argument {} must declare num_args(1)",
                    arg.id
                )));
            }
            if arg.action != ArgAction::Set && arg.num_args != 0 {
                return Err(Error::configuration(format!(
                    "flag argument {} must not consume a value",
                    arg.id
                )));
            }
        }
        for subcommand in &self.subcommands {
            subcommand.validate()?;
        }
        Ok(())
    }

    pub fn try_get_matches_from<I, S>(&self, values: I) -> Result<ArgMatches, Error>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.validate()?;
        let tokens: Vec<String> = values.into_iter().map(Into::into).collect();
        let mut root = MatchValues::default();
        let mut child: Option<(&Command, MatchValues)> = None;
        let mut index = 1;
        while index < tokens.len() {
            let token = &tokens[index];
            if child.is_none() {
                if let Some(found) = self.subcommands.iter().find(|sub| sub.name == *token) {
                    child = Some((found, MatchValues::default()));
                    index += 1;
                    continue;
                }
            }

            if token.starts_with('-') {
                let selected = child.as_ref().map(|(command, _)| *command);
                let (arg, attached, short_count) = self.find_option(selected, token)?;
                let target = if arg.global {
                    &mut root
                } else {
                    &mut child
                        .as_mut()
                        .ok_or_else(|| Error::usage(format!("unexpected argument '{token}'")))?
                        .1
                };
                match arg.action {
                    ArgAction::Set => {
                        let value = if let Some(value) = attached {
                            value
                        } else {
                            index += 1;
                            tokens.get(index).cloned().ok_or_else(|| {
                                Error::usage(format!("a value is required for '--{}'", arg.long.as_deref().unwrap_or(&arg.id)))
                            })?
                        };
                        target.values.insert(arg.id.clone(), value);
                    }
                    ArgAction::SetTrue => {
                        target.flags.insert(arg.id.clone(), true);
                    }
                    ArgAction::Count => {
                        *target.counts.entry(arg.id.clone()).or_default() += short_count;
                    }
                }
                index += 1;
                continue;
            }

            let (command, values) = child
                .as_mut()
                .ok_or_else(|| Error::usage(format!("unrecognized subcommand '{token}'")))?;
            let positional = command
                .args
                .iter()
                .find(|arg| arg.long.is_none() && !values.values.contains_key(&arg.id))
                .ok_or_else(|| Error::usage(format!("unexpected argument '{token}'")))?;
            values.values.insert(positional.id.clone(), token.clone());
            index += 1;
        }

        self.apply_defaults_and_required(&mut root)?;
        let subcommand = if let Some((command, mut values)) = child {
            command.apply_defaults_and_required(&mut values)?;
            command.check_conflicts(&values)?;
            Some((command.name.clone(), Box::new(values.into_matches(None))))
        } else {
            if self.require_subcommand {
                return Err(Error::usage("a subcommand is required"));
            }
            None
        };
        self.check_conflicts(&root)?;
        Ok(root.into_matches(subcommand))
    }

    pub fn render_help(&self) -> Result<String, Error> {
        self.validate()?;
        let mut output = format!(
            "{} {}\n{}\n\nUsage: {} [OPTIONS] <COMMAND>\n\nOptions:\n",
            self.name, self.version, self.about, self.name
        );
        for arg in &self.args {
            output.push_str(&self.help_line(arg));
        }
        output.push_str("  -h, --help               Print help\n\nCommands:\n");
        for command in &self.subcommands {
            output.push_str(&format!("  {:<12} {}\n", command.name, command.about));
        }
        Ok(output)
    }

    pub fn render_subcommand_help(&self, name: &str) -> Result<String, Error> {
        self.validate()?;
        let command = self
            .subcommands
            .iter()
            .find(|candidate| candidate.name == name)
            .ok_or_else(|| Error::usage(format!("unrecognized subcommand '{name}'")))?;
        let positional = command.args.iter().find(|arg| arg.long.is_none());
        let suffix = positional
            .map(|arg| format!(" <{}>", arg.value_name.clone().unwrap_or_else(|| arg.id.to_uppercase())))
            .unwrap_or_default();
        let mut output = format!(
            "{}\n\nUsage: {} {} [OPTIONS]{}\n\nOptions:\n",
            command.about, self.name, command.name, suffix
        );
        for arg in &command.args {
            if arg.long.is_some() {
                output.push_str(&self.help_line(arg));
            }
        }
        output.push_str("  -h, --help               Print help\n");
        Ok(output)
    }

    fn help_line(&self, arg: &Arg) -> String {
        let default = arg
            .default
            .as_ref()
            .map(|value| format!(" [default: {value}]"))
            .unwrap_or_default();
        format!("  {:<27} {}{}\n", arg.option_label(), arg.help, default)
    }

    fn find_option<'a>(
        &'a self,
        selected: Option<&'a Command>,
        token: &str,
    ) -> Result<(&'a Arg, Option<String>, u8), Error> {
        let candidates = selected
            .into_iter()
            .flat_map(|command| command.args.iter())
            .chain(self.args.iter().filter(|arg| arg.global));
        if let Some(long) = token.strip_prefix("--") {
            let (name, attached) = long
                .split_once('=')
                .map(|(name, value)| (name, Some(value.to_string())))
                .unwrap_or((long, None));
            let arg = candidates
                .into_iter()
                .find(|arg| arg.long.as_deref() == Some(name))
                .ok_or_else(|| Error::usage(format!("unexpected argument '--{name}'")))?;
            return Ok((arg, attached, 1));
        }
        let shorts = token
            .strip_prefix('-')
            .filter(|value| !value.is_empty())
            .ok_or_else(|| Error::usage(format!("unexpected argument '{token}'")))?;
        let first = shorts.chars().next().unwrap();
        let arg = candidates
            .into_iter()
            .find(|arg| arg.short == Some(first))
            .ok_or_else(|| Error::usage(format!("unexpected argument '-{first}'")))?;
        if shorts.chars().any(|value| value != first) || (shorts.len() > 1 && arg.action != ArgAction::Count) {
            return Err(Error::usage(format!("unexpected short option group '-{shorts}'")));
        }
        Ok((arg, None, shorts.len() as u8))
    }

    fn apply_defaults_and_required(&self, values: &mut MatchValues) -> Result<(), Error> {
        for arg in &self.args {
            if let Some(default) = &arg.default {
                values.values.entry(arg.id.clone()).or_insert_with(|| default.clone());
            }
            if arg.required && !values.values.contains_key(&arg.id) {
                return Err(Error::usage(format!(
                    "a value is required for <{}>",
                    arg.value_name.clone().unwrap_or_else(|| arg.id.to_uppercase())
                )));
            }
        }
        Ok(())
    }

    fn check_conflicts(&self, values: &MatchValues) -> Result<(), Error> {
        for arg in &self.args {
            if !values.is_present(&arg.id) {
                continue;
            }
            for conflict in &arg.conflicts {
                if values.is_present(conflict) {
                    return Err(Error::usage(format!(
                        "argument '--{}' cannot be used with '--{}'",
                        arg.long.as_deref().unwrap_or(&arg.id), conflict
                    )));
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug, Default)]
struct MatchValues {
    values: HashMap<String, String>,
    flags: HashMap<String, bool>,
    counts: HashMap<String, u8>,
}

impl MatchValues {
    fn is_present(&self, id: &str) -> bool {
        self.values.contains_key(id)
            || self.flags.get(id).copied().unwrap_or(false)
            || self.counts.get(id).copied().unwrap_or(0) > 0
    }

    fn into_matches(self, subcommand: Option<(String, Box<ArgMatches>)>) -> ArgMatches {
        ArgMatches {
            values: self.values,
            flags: self.flags,
            counts: self.counts,
            subcommand,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ArgMatches {
    values: HashMap<String, String>,
    flags: HashMap<String, bool>,
    counts: HashMap<String, u8>,
    subcommand: Option<(String, Box<ArgMatches>)>,
}

impl ArgMatches {
    pub fn get_one(&self, id: &str) -> Option<&String> {
        self.values.get(id)
    }

    pub fn get_flag(&self, id: &str) -> bool {
        self.flags.get(id).copied().unwrap_or(false)
    }

    pub fn get_count(&self, id: &str) -> u8 {
        self.counts.get(id).copied().unwrap_or(0)
    }

    pub fn subcommand(&self) -> Option<(&str, &ArgMatches)> {
        self.subcommand
            .as_ref()
            .map(|(name, matches)| (name.as_str(), matches.as_ref()))
    }

    pub fn value_of(&self, _id: &str) -> Option<&str> {
        panic!("removed ArgMatches::value_of accessor invoked")
    }

    pub fn occurrences_of(&self, _id: &str) -> u64 {
        panic!("removed ArgMatches::occurrences_of accessor invoked")
    }

    pub fn subcommand_name(&self) -> Option<&str> {
        panic!("removed ArgMatches::subcommand_name accessor invoked")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub kind: ErrorKind,
    pub message: String,
    pub exit_code: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    Usage,
    Configuration,
}

impl Error {
    fn usage(message: impl Into<String>) -> Self {
        Self { kind: ErrorKind::Usage, message: message.into(), exit_code: 2 }
    }

    fn configuration(message: impl Into<String>) -> Self {
        Self { kind: ErrorKind::Configuration, message: message.into(), exit_code: 70 }
    }
}
