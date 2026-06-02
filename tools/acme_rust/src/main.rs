use std::env;
use std::process;

fn main() {
    match env::args().nth(1).as_deref() {
        Some("ping") => println!("rust-ok"),
        Some("health") => println!(r#"{{"runtime":"rust","ok":true}}"#),
        _ => {
            eprintln!("usage: acme-rust <ping|health>");
            process::exit(2);
        }
    }
}
