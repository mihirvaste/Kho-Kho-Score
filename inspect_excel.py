import zipfile
from pathlib import Path
p = Path('score-sheet/Kho_Kho_Score_Sheet.xlsx')
print('exists', p.exists(), p.stat().st_size)
with zipfile.ZipFile(p) as z:
    print('\n'.join(z.namelist()))
    for name in z.namelist():
        if name.endswith('.xml'):
            data = z.read(name).decode('utf-8')
            print('---', name, '---')
            print(data[:4000])
            print()
