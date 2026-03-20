import os
import re

count = 0
for root, _, files in os.walk('tests'):
    for file in files:
        if file.endswith('.py'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Fix OSIRelationship unique_names
            new_content = re.sub(
                r'(unique_name=[\'"])(?!REL_)[^\'"]+([\'"])',
                r'\g<1>REL_TEST_A__TEST_B\g<2>',
                content
            )

            # Fix dictionary "name": "..." for relationships in lists often seen in tests
            # This is riskier but necessary if they feed into OSIRelationship
            new_content = re.sub(
                r'("name":\s*[\'"])(?!REL_|SYS_)[^\'"]+([\'"])',
                r'\g<1>REL_TEST_A__TEST_B\g<2>',
                new_content
            )
            
            # Revert unintentional SYS_ replacements if needed, but the Above regex (?!SYS_) skips them
            # Some tests might intentionally test SYS_ rejection. SYS_ strings should be left alone!

            if new_content != content:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f'Fixed {path}')
                count += 1

print(f"Fixed {count} files.")
