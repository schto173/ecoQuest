use rppal::gpio::{Gpio, Trigger};
use std::time::{Instant, Duration};
use std::error::Error;
use serde_json::json;
use std::sync::atomic::{Ordering, AtomicBool};
use std::sync::{Arc, mpsc};
use signal_hook::{consts::SIGINT, iterator::Signals};
use std::process::Command;
use std::fs::OpenOptions;
use std::io::Write;
use std::collections::VecDeque;

const GPIO_PIN: u8 = 17;
const TIMEOUT_SECS: u64 = 2;
const DEBOUNCE_MS: u64 = 20;
const STATUS_FILE: &str = "/tmp/wheel_speed.json";
const RPM_BUFFER_SIZE: usize = 3;  // Average over last 3 readings for smoother output

// Helper function for non-blocking file write
fn write_status_nonblocking(status: serde_json::Value) -> Result<(), Box<dyn Error>> {
    let file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(STATUS_FILE)?;
    
    let mut writer = std::io::BufWriter::new(file);
    serde_json::to_writer(&mut writer, &status)?;
    writer.flush()?;
    
    Ok(())
}

fn cleanup_gpio(pin: u8) {
    Command::new("sh")
        .arg("-c")
        .arg(format!("echo {} > /sys/class/gpio/unexport 2>/dev/null || true", pin))
        .output()
        .ok();
}

fn main() -> Result<(), Box<dyn Error>> {
    println!("Initializing GPIO...");
    cleanup_gpio(GPIO_PIN);
    std::thread::sleep(Duration::from_millis(100));
    
    let gpio = Gpio::new()?;
    let mut pin = match gpio.get(GPIO_PIN) {
        Ok(p) => p.into_input_pullup(),
        Err(e) => {
            eprintln!("Failed to access GPIO {}: {}", GPIO_PIN, e);
            cleanup_gpio(GPIO_PIN);
            return Err(e.into());
        }
    };
    
    // Create channel for file writing
    let (tx, rx) = mpsc::channel();
    
    // Spawn file writer thread
    std::thread::spawn(move || {
        while let Ok(status) = rx.recv() {
            if let Err(e) = write_status_nonblocking(status) {
                eprintln!("Failed to write status: {}", e);
            }
        }
    });
    
    let mut last_time = Instant::now();
    let mut counter = 0;
    let mut current_rpm = 0.0;
    let mut rpm_buffer = VecDeque::with_capacity(RPM_BUFFER_SIZE);
    
    println!("Monitoring wheel sensor on GPIO {}...", GPIO_PIN);
    println!("Debounce time: {}ms", DEBOUNCE_MS);
    println!("Press Ctrl+C to exit");

    pin.set_interrupt(Trigger::FallingEdge, Some(Duration::from_millis(DEBOUNCE_MS)))?;

    // Handle Ctrl+C
    let mut signals = Signals::new(&[SIGINT])?;
    let running = Arc::new(AtomicBool::new(true));
    let running_clone = running.clone();

    std::thread::spawn(move || {
        for _ in signals.forever() {
            running_clone.store(false, Ordering::SeqCst);
            break;
        }
    });

    // Initialize status file
    tx.send(json!({
        "rpm": 0.0,
        "count": 0,
        "timestamp": Instant::now().elapsed().as_secs(),
        "running": true
    }))?;

    while running.load(Ordering::SeqCst) {
        if pin.poll_interrupt(false, Some(Duration::from_millis(2000)))?.is_some() {
            let now = Instant::now();
            let duration = now.duration_since(last_time);
            let instant_rpm = 60.0 / duration.as_secs_f64();
            
            // Update RPM buffer for averaging
            rpm_buffer.push_back(instant_rpm);
            if rpm_buffer.len() > RPM_BUFFER_SIZE {
                rpm_buffer.pop_front();
            }
            
            // Calculate average RPM
            current_rpm = rpm_buffer.iter().sum::<f64>() / rpm_buffer.len() as f64;
            
            counter += 1;
            println!("Rotation {}: {:.1} RPM", counter, current_rpm);
            
            tx.send(json!({
                "rpm": current_rpm,
                "count": counter,
                "timestamp": now.elapsed().as_secs(),
                "running": true
            }))?;
            
            last_time = now;
        } else if current_rpm != 0.0 {
            current_rpm = 0.0;
            rpm_buffer.clear();  // Clear the buffer when stopping
            println!("Speed: 0.0 RPM (no rotation for {} seconds)", TIMEOUT_SECS);
            
            tx.send(json!({
                "rpm": 0.0,
                "count": counter,
                "timestamp": Instant::now().elapsed().as_secs(),
                "running": true
            }))?;
        }
    }

    // Final update
    tx.send(json!({
        "rpm": current_rpm,
        "count": counter,
        "timestamp": Instant::now().elapsed().as_secs(),
        "running": false
    }))?;

    cleanup_gpio(GPIO_PIN);
    Ok(())
}
