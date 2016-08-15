python patcher.py
arm-none-eabi-objdump -d patch.comp.o > patch.d
arm-none-eabi-objdump -marm -Mforce-thumb -b binary -D final.bin > final.d