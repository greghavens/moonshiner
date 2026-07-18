use std::io::{self, BufRead};

use checkpoint_agent::run_line;
use checkpoint_facade::Gateway;

fn main() {
    let stdin = io::stdin();
    let mut gateway = Gateway::new();
    for line in stdin.lock().lines() {
        match line {
            Ok(line) => println!("{}", run_line(&mut gateway, &line)),
            Err(error) => {
                eprintln!("checkpoint-agent: {error}");
                std::process::exit(1);
            }
        }
    }
}
