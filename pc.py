from Crypto.Cipher import AES
from tkinter import filedialog
# === KEY (R8 dump) ===
key_material = bytes.fromhex(
    "41 33 66 7A 67 39 38 75 30 63 50 4F 34 64 67 72"
    "6E 6A 45 30 6F 37 38 69 65 34 33 67 37 35 77 32"
    "67 72 65 64 43 56 6E 47 67 72 45 52 73 69 67 36"
    "35 6A 68 37 6C 6F 67 65 74 6F 33 73 47 52 6A 6F"
)

aes_key = key_material[:32]

cipher = AES.new(aes_key, AES.MODE_ECB)


def decrypt_file(in_path):
    with open(in_path, "rb") as f:
        data = f.read()

    out = bytearray()

    # same loop as game: 16-byte blocks
    for i in range(0, len(data), 16):
        block = data[i:i+16]

        if len(block) < 16:

            out += block
            break

        dec = cipher.decrypt(block)
        out += dec
    
    return out


def encrypt_file(in_path):
    with open(in_path, "rb") as f:
        data = f.read()

    out = bytearray()

    for i in range(0, len(data), 16):
        block = data[i:i+16]

        if len(block) < 16:
            block = block.ljust(16, b"\x00")

        enc = cipher.encrypt(block)
        out += enc

    return out



if __name__ == "__main__":
    import sys
    file_path=filedialog.askopenfilename()
    decrypt_file(file_path, file_path+'dec')
    #encrypt_file(file_path, file_path+'enc')
    
