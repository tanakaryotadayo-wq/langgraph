// SPDX-License-Identifier: Apache-2.0
use std::io::{self, BufRead};
use serde_json::{Value, json};

fn main() {
    for line in io::stdin().lock().lines() {
        let result = line.map_err(|e| e.to_string()).and_then(|line| {
            let input: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
            let text = input["text"].as_str().ok_or("text must be a string")?;
            let budget = input["max_tokens"].as_u64().ok_or("max_tokens must be nonnegative")? as usize;
            let (retained, original) = codex_utils_string::truncate_middle_with_token_budget(text, budget);
            Ok(json!({"text":retained,"original_tokens":original,
                "approx_tokens":codex_utils_string::approx_token_count(text),
                "changed":original.is_some()}))
        });
        match result {
            Ok(value) => println!("{value}"),
            Err(error) => { eprintln!("{error}"); std::process::exit(1); }
        }
    }
}
