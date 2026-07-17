// Acceptance tests for the typed config loader.
// Positions are 1-based (line, col) counting characters; every error carries
// one. Errors are asserted structurally — variant, payload, and position.

use rs_cfgparse::{
    load_server_config, ConfigError, FieldError, FieldKind, LogLevel, ParseError, ParseKind,
    RawConfig, ServerConfig,
};

fn parse_err(line: usize, col: usize, kind: ParseKind) -> ConfigError {
    ConfigError::Parse(ParseError { line, col, kind })
}

fn field_err(line: usize, col: usize, key: &str, kind: FieldKind) -> ConfigError {
    ConfigError::Field(FieldError {
        line,
        col,
        section: "server".to_string(),
        key: key.to_string(),
        kind,
    })
}

#[test]
fn happy_path_loads_every_field() {
    let src = "# edge tier\n\n[server]\nhost = edge-1.prod.internal\nport = 8443\ntls = true\nworkers = 32\nlog_level = warn\nallow = 10.0.0.0/8, fe80::/10\n\n[limits]\nrps = 400\n";
    assert_eq!(
        load_server_config(src),
        Ok(ServerConfig {
            host: "edge-1.prod.internal".to_string(),
            port: 8443,
            tls: true,
            workers: 32,
            log_level: LogLevel::Warn,
            allow: vec!["10.0.0.0/8".to_string(), "fe80::/10".to_string()],
        })
    );
}

#[test]
fn optional_fields_fall_back_to_defaults() {
    let src = "[server]\nhost = localhost\nport = 8080\n";
    assert_eq!(
        load_server_config(src),
        Ok(ServerConfig {
            host: "localhost".to_string(),
            port: 8080,
            tls: false,
            workers: 4,
            log_level: LogLevel::Info,
            allow: vec![],
        })
    );
}

#[test]
fn raw_layer_reports_value_positions() {
    let src = "[server]\nhost = api.internal\nhost_tab\t=\tapi2\n";
    // avoid tripping the duplicate-key rule: second key differs
    let raw = RawConfig::parse(src).expect("parses");
    assert_eq!(raw.section_pos("server"), Some((1, 1)));
    assert_eq!(raw.section_pos("nope"), None);

    let host = raw.get("server", "host").expect("host present");
    assert_eq!(host.text, "api.internal");
    assert_eq!((host.line, host.col), (2, 8));

    let tabbed = raw.get("server", "host_tab").expect("tabbed present");
    assert_eq!(tabbed.text, "api2");
    assert_eq!((tabbed.line, tabbed.col), (3, 12), "tabs count as one column");

    assert!(raw.get("server", "missing").is_none());
    assert!(raw.get("db", "host").is_none());
}

#[test]
fn values_keep_hashes_and_inner_spacing() {
    // comments only start at the beginning of a line — '#' inside a value is data
    let src = "[server]\nbanner = hello world # not a comment\n  # a real comment\nhost = a\nport = 1\n";
    let raw = RawConfig::parse(src).expect("parses");
    assert_eq!(
        raw.get("server", "banner").unwrap().text,
        "hello world # not a comment"
    );
    // unknown keys are ignored by typed extraction
    assert!(load_server_config(src).is_ok());
}

#[test]
fn crlf_input_parses_with_clean_values() {
    let src = "[server]\r\nhost = api\r\nport = 9000\r\n";
    let cfg = load_server_config(src).expect("CRLF should be tolerated");
    assert_eq!(cfg.host, "api");
    assert_eq!(cfg.port, 9000);
    let raw = RawConfig::parse(src).unwrap();
    assert_eq!(raw.get("server", "port").unwrap().text, "9000");
    assert_eq!((raw.get("server", "port").unwrap().line, raw.get("server", "port").unwrap().col), (3, 8));
}

#[test]
fn missing_required_key_points_at_the_section_header() {
    // header is indented: its '[' sits at line 3, col 3
    let src = "# prod\n\n  [server]\n  host = api.internal\n";
    assert_eq!(
        load_server_config(src),
        Err(field_err(3, 3, "port", FieldKind::Missing))
    );
}

#[test]
fn missing_section_reports_line_one_col_one_for_the_first_field() {
    let src = "[db]\nurl = postgres://x\n";
    assert_eq!(
        load_server_config(src),
        Err(field_err(1, 1, "host", FieldKind::Missing))
    );
    // extraction order is host, port, tls, workers, log_level, allow —
    // with several fields missing, host is the one reported
    let src2 = "[server]\ntls = true\n";
    assert_eq!(
        load_server_config(src2),
        Err(field_err(1, 1, "host", FieldKind::Missing))
    );
}

#[test]
fn wrong_type_integer_carries_the_value_position() {
    let src = "[server]\nhost = a\nport = fast\n";
    assert_eq!(
        load_server_config(src),
        Err(field_err(
            3,
            8,
            "port",
            FieldKind::WrongType {
                expected: "integer",
                got: "fast".to_string()
            }
        ))
    );
}

#[test]
fn wrong_type_boolean_is_strict() {
    let src = "[server]\nhost = a\nport = 80\ntls = yes\n";
    assert_eq!(
        load_server_config(src),
        Err(field_err(
            4,
            7,
            "tls",
            FieldKind::WrongType {
                expected: "boolean",
                got: "yes".to_string()
            }
        ))
    );
    // "True" is not "true"
    let src2 = "[server]\nhost = a\nport = 80\ntls = True\n";
    assert!(matches!(
        load_server_config(src2),
        Err(ConfigError::Field(FieldError {
            kind: FieldKind::WrongType { expected: "boolean", .. },
            ..
        }))
    ));
}

#[test]
fn port_range_is_enforced() {
    let range_err = |line: usize| {
        field_err(
            line,
            8,
            "port",
            FieldKind::BadValue("port must be between 1 and 65535".to_string()),
        )
    };
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = 0\n"),
        Err(range_err(3))
    );
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = 65536\n"),
        Err(range_err(3))
    );
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = -1\n"),
        Err(range_err(3))
    );
    // all-digits but far beyond i64 still lands in the range error, not a crash
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = 99999999999999999999\n"),
        Err(range_err(3))
    );
    let max = load_server_config("[server]\nhost = a\nport = 65535\n").expect("65535 is valid");
    assert_eq!(max.port, 65535);
}

#[test]
fn workers_range_is_enforced() {
    let range_err = || FieldKind::BadValue("workers must be between 1 and 512".to_string());
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = 1\nworkers = 0\n"),
        Err(field_err(4, 11, "workers", range_err()))
    );
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = 1\nworkers = 513\n"),
        Err(field_err(4, 11, "workers", range_err()))
    );
    let ok = load_server_config("[server]\nhost = a\nport = 1\nworkers = 512\n").unwrap();
    assert_eq!(ok.workers, 512);
}

#[test]
fn log_levels_map_exactly() {
    for (text, level) in [
        ("error", LogLevel::Error),
        ("warn", LogLevel::Warn),
        ("info", LogLevel::Info),
        ("debug", LogLevel::Debug),
    ] {
        let src = format!("[server]\nhost = a\nport = 1\nlog_level = {text}\n");
        assert_eq!(load_server_config(&src).unwrap().log_level, level);
    }
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = 1\nlog_level = verbose\n"),
        Err(field_err(
            4,
            13,
            "log_level",
            FieldKind::BadValue("unknown log level \"verbose\"".to_string())
        ))
    );
}

#[test]
fn allow_list_splits_on_commas_and_trims() {
    let src = "[server]\nhost = a\nport = 1\nallow = 10.0.0.0/8, 192.168.0.0/16 ,fe80::/10\n";
    assert_eq!(
        load_server_config(src).unwrap().allow,
        vec![
            "10.0.0.0/8".to_string(),
            "192.168.0.0/16".to_string(),
            "fe80::/10".to_string()
        ]
    );
    let empty_kind = || FieldKind::BadValue("empty entry in allow list".to_string());
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = 1\nallow = a,,b\n"),
        Err(field_err(4, 9, "allow", empty_kind()))
    );
    assert_eq!(
        load_server_config("[server]\nhost = a\nport = 1\nallow = a,b,\n"),
        Err(field_err(4, 9, "allow", empty_kind()))
    );
}

#[test]
fn duplicate_keys_error_at_the_second_occurrence() {
    let src = "[server]\nhost = a\nport = 1\nhost = b\n";
    assert_eq!(
        load_server_config(src),
        Err(parse_err(4, 1, ParseKind::DuplicateKey("host".to_string())))
    );
    // the same key in different sections is fine
    let ok = "[server]\nhost = a\nport = 1\n[db]\nhost = b\n";
    assert!(load_server_config(ok).is_ok());
    let raw = RawConfig::parse(ok).unwrap();
    assert_eq!(raw.get("db", "host").unwrap().text, "b");
}

#[test]
fn reopening_a_section_accumulates_keys() {
    let src = "[server]\nhost = a\n[limits]\nrps = 5\n[server]\nport = 9\n";
    let cfg = load_server_config(src).expect("reopened section merges");
    assert_eq!((cfg.host.as_str(), cfg.port), ("a", 9));

    let dup = "[server]\nhost = a\n[server]\nhost = b\n";
    assert_eq!(
        load_server_config(dup),
        Err(parse_err(4, 1, ParseKind::DuplicateKey("host".to_string())))
    );
}

#[test]
fn syntax_errors_carry_exact_positions() {
    assert_eq!(
        load_server_config("[server]\nhost = a\nwhat is this\n"),
        Err(parse_err(3, 1, ParseKind::BadLine))
    );
    assert_eq!(
        load_server_config("[server\nhost = a\n"),
        Err(parse_err(1, 1, ParseKind::UnclosedSection))
    );
    assert_eq!(
        load_server_config("  [server\n"),
        Err(parse_err(1, 3, ParseKind::UnclosedSection))
    );
    assert_eq!(
        load_server_config("[server] prod\nhost = a\n"),
        Err(parse_err(1, 10, ParseKind::TrailingChars))
    );
    assert_eq!(
        load_server_config("[server]\nport =\n"),
        Err(parse_err(2, 6, ParseKind::MissingValue))
    );
    assert_eq!(
        load_server_config("[server]\nport =   \n"),
        Err(parse_err(2, 6, ParseKind::MissingValue))
    );
    assert_eq!(
        load_server_config("[server]\n= 5\n"),
        Err(parse_err(2, 1, ParseKind::BadLine))
    );
    assert_eq!(
        load_server_config("port = 80\n[server]\nhost = a\n"),
        Err(parse_err(1, 1, ParseKind::KeyOutsideSection))
    );
    assert_eq!(
        load_server_config("  port = 80\n[server]\n"),
        Err(parse_err(1, 3, ParseKind::KeyOutsideSection))
    );
}

#[test]
fn from_conversions_wrap_both_error_layers() {
    let pe = ParseError {
        line: 2,
        col: 5,
        kind: ParseKind::BadLine,
    };
    let wrapped: ConfigError = pe.clone().into();
    assert_eq!(wrapped, ConfigError::Parse(pe));
    assert_eq!((wrapped.line(), wrapped.col()), (2, 5));

    let fe = FieldError {
        line: 7,
        col: 3,
        section: "server".to_string(),
        key: "port".to_string(),
        kind: FieldKind::Missing,
    };
    let wrapped: ConfigError = fe.clone().into();
    assert_eq!(wrapped, ConfigError::Field(fe));
    assert_eq!((wrapped.line(), wrapped.col()), (7, 3));
}

#[test]
fn display_messages_are_stable() {
    let dup = parse_err(4, 1, ParseKind::DuplicateKey("host".to_string()));
    assert_eq!(dup.to_string(), "line 4, col 1: duplicate key \"host\"");

    let wrong = field_err(
        3,
        8,
        "port",
        FieldKind::WrongType {
            expected: "integer",
            got: "fast".to_string(),
        },
    );
    assert_eq!(
        wrong.to_string(),
        "line 3, col 8: server.port: expected integer, got \"fast\""
    );

    let missing = field_err(3, 3, "port", FieldKind::Missing);
    assert_eq!(
        missing.to_string(),
        "line 3, col 3: server.port: missing required key"
    );

    fn assert_error<E: std::error::Error>(_: &E) {}
    assert_error(&dup);
}
