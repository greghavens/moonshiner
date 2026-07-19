use crate::command::command;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Plan {
    Serve {
        profile: String,
        bind: String,
        port: u16,
        json: bool,
        verbosity: u8,
    },
    Completion { profile: String, shell: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CliOutput {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
    pub plan: Option<Plan>,
}

pub fn run(args: &[&str]) -> CliOutput {
    let definition = command();
    if args.iter().any(|value| *value == "--help" || *value == "-h") {
        let rendered = args
            .iter()
            .find(|value| **value == "serve" || **value == "completion")
            .map(|name| definition.render_subcommand_help(name))
            .unwrap_or_else(|| definition.render_help());
        return match rendered {
            Ok(stdout) => CliOutput { code: 0, stdout, stderr: String::new(), plan: None },
            Err(error) => CliOutput {
                code: error.exit_code,
                stdout: String::new(),
                stderr: format!("error: {}\n", error.message),
                plan: None,
            },
        };
    }

    let matches = match definition.try_get_matches_from(args.iter().copied()) {
        Ok(matches) => matches,
        Err(error) => {
            return CliOutput {
                code: error.exit_code,
                stdout: String::new(),
                stderr: format!("error: {}\n", error.message),
                plan: None,
            }
        }
    };

    let profile = matches.value_of("profile").unwrap_or("development").to_string();
    let plan = match matches.subcommand_name() {
        Some("serve") => {
            let (_, serve) = matches.subcommand().expect("serve matches");
            let port_text = serve.value_of("port").unwrap_or("8080");
            let port = match port_text.parse::<u16>() {
                Ok(port) if port > 0 => port,
                _ => {
                    return CliOutput {
                        code: 2,
                        stdout: String::new(),
                        stderr: format!("error: invalid value '{port_text}' for '--port'\n"),
                        plan: None,
                    }
                }
            };
            Plan::Serve {
                profile,
                bind: serve.value_of("bind").unwrap_or("127.0.0.1").to_string(),
                port,
                json: serve.value_of("json").is_some(),
                verbosity: serve.occurrences_of("verbose") as u8,
            }
        }
        Some("completion") => {
            let (_, completion) = matches.subcommand().expect("completion matches");
            Plan::Completion {
                profile,
                shell: completion.value_of("shell").unwrap_or_default().to_string(),
            }
        }
        _ => unreachable!("the command requires a known subcommand"),
    };
    CliOutput { code: 0, stdout: String::new(), stderr: String::new(), plan: Some(plan) }
}
