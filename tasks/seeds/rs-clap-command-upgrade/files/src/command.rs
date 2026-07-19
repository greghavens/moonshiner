use crate::clap_v4::{Arg, Command};

pub fn command() -> Command {
    Command::new("dockctl")
        .version("4.5.0")
        .about("Operate the local document gateway")
        .subcommand_required(true)
        .arg(
            Arg::new("profile")
                .long("profile")
                .value_name("PROFILE")
                .help("Runtime profile")
                .default_value("development")
                .global(true)
                .takes_value(true),
        )
        .subcommand(
            Command::new("serve")
                .about("Start the gateway")
                .arg(
                    Arg::new("bind")
                        .long("bind")
                        .short('b')
                        .value_name("ADDRESS")
                        .help("Listen address")
                        .default_value("127.0.0.1")
                        .takes_value(true),
                )
                .arg(
                    Arg::new("port")
                        .long("port")
                        .short('p')
                        .value_name("PORT")
                        .help("Listen port")
                        .default_value("8080")
                        .takes_value(true),
                )
                .arg(
                    Arg::new("json")
                        .long("json")
                        .help("Emit JSON logs")
                        .conflicts_with("verbose")
                        .takes_value(false),
                )
                .arg(
                    Arg::new("verbose")
                        .long("verbose")
                        .short('v')
                        .help("Increase verbosity")
                        .multiple_occurrences(true),
                ),
        )
        .subcommand(
            Command::new("completion")
                .about("Print shell completion")
                .arg(
                    Arg::new("shell")
                        .value_name("SHELL")
                        .required(true)
                        .takes_value(true),
                ),
        )
}
