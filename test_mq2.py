import lgpio
import time

GPIO_PIN = 4
handle = lgpio.gpiochip_open(0)
lgpio.gpio_claim_input(handle, GPIO_PIN)

print("MQ2 Gas Sensor Monitor (Ctrl+C to stop)\n")
print("Tip: Adjust potentiometer on module to set sensitivity\n")

try:
    while True:
        value = lgpio.gpio_read(handle, GPIO_PIN)
        if value == 0:
            print("🚨 GAS LEAKAGE DETECTED!")
        else:
            print("✅ Air Clear ")
        time.sleep(1)

except KeyboardInterrupt:
    print("\nStopped.")

finally:
    lgpio.gpiochip_close(handle)
