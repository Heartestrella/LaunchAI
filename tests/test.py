import hashlib
import os


def get_folder_content_md5(folder_path: str) -> str:
    md5 = hashlib.md5()
    if not folder_path or not os.path.isdir(folder_path):
        return ""
    try:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if d not in {
                '.git', '__pycache__', 'node_modules'}]

            for file in sorted(files):
                file_path = os.path.join(root, file)
                with open(file_path, 'rb') as f:
                    while chunk := f.read(8192):
                        md5.update(chunk)

        return md5.hexdigest()
    except Exception as e:
        return ""


print(get_folder_content_md5(
    r"C:\Users\Administrator\Desktop\LaunchAI\_git_projects\Applio_3.6.2"))
