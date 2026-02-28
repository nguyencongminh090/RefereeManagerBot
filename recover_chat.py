import sys
import os
import re

file_path = os.path.expanduser("~/.gemini/antigravity/conversations/413e0152-ed5c-4526-a2bc-2d14628426ca.pb")
output_file = os.path.expanduser("~/Desktop/Recovered_Referee_Chat.md")

if not os.path.exists(file_path):
    print(f"Error: File not found at {file_path}")
    sys.exit(1)

print(f"Reading {file_path}...")

with open(file_path, 'rb') as f:
    content = f.read()

# Extract strings and filter out short binary garbage
strings = re.findall(rb'[^\x00-\x1f\x7f-\xff]{15,}', content)

with open(output_file, 'w') as f:
    f.write("# Recovered Chat Log: 413e0152\n\n")
    for s in strings:
        try:
            line = s.decode('utf-8').strip()
            # Basic cleanup of common PB artifacts
            if len(line) > 20:
                f.write(f"{line}\n\n---\n\n")
        except:
            continue

print(f"Done! Your chat history is now readable at: {output_file}")
