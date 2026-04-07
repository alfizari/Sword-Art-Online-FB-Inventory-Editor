import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import struct, json, shutil, os, copy
import pc as PC

# ── Constants ─────────────────────────────────────────────────────────────────
ITEM_SPACING_MIN = 0x30
ITEM_SPACING_MAX = 0x70
HAS_CHIPS = {'weapon', 'costume', 'accessory'}

CAT_LABELS = {
    'all':       'All Items',
    'weapon':    'Weapons',
    'costume':   'Costumes',
    'accessory': 'Accessories',
    'bullet':    'Bullets',
    'material':  'Materials',
    'other':     'Other',
}
CAT_ORDER = ['all', 'weapon', 'costume', 'accessory', 'bullet', 'material', 'other']

CAT_COLORS = {
    'weapon':    '#e07070',
    'costume':   '#7090d0',
    'accessory': '#d0a040',
    'bullet':    '#50b880',
    'material':  '#a070d0',
    'other':     '#888888',
}

MODE = None

# ── File open/save ────────────────────────────────────────────────────────────
def open_file(file_path):
    global MODE
    with open(file_path, 'rb') as f:
        data = f.read()
    if data[:4] == bytes.fromhex('01000000'):
        MODE = 'PS4'
    else:
        MODE = 'PC'
        data = PC.decrypt_file(file_path)
        edited_path = file_path + '.edited'
        shutil.copy2(file_path, edited_path)
        with open(edited_path, 'wb') as f:
            f.write(data)
    return bytearray(data)

def save_file(file_path, data):
    global MODE
    if MODE == 'PC':
        enc_data = PC.encrypt_file(file_path)
        with open(file_path, 'wb') as f:
            f.write(enc_data)
    else:
        with open(file_path, 'wb') as f:
            f.write(data)




# ── Save parsing ──────────────────────────────────────────────────────────────
def item_category(key):
    if key.startswith('W'): return 'weapon'
    if key.startswith('C'): return 'costume'
    if key.startswith('A'): return 'accessory'
    if key.startswith('M'): return 'material'
    if key.startswith('B'): return 'bullet'
    return 'other'

def main_character(data):
    char_name_offset = 0x35
    name_len = struct.unpack_from('<I', data, char_name_offset)[0]
    slot_bytes = bytes(data[char_name_offset: char_name_offset + 4 + name_len])
    aob = bytes.fromhex("00000002000000000000000000000000")
    offset = 0
    while True:
        offset = bytes(data).find(slot_bytes, offset)
        if offset == -1:
            return None
        if offset >= len(aob) and bytes(data[offset - len(aob): offset]) == aob:
            return offset
        offset += 1

def find_inventory_bounds(data, items_dict):
    player_offset = main_character(data)
    if player_offset is None:
        return None, None
    search_region = bytes(data[player_offset:])
    items_bytes = {key: key.encode('ascii') for key in items_dict}
    hit_positions = {}
    for key, b_key in items_bytes.items():
        pos = 0
        while True:
            pos = search_region.find(b_key, pos)
            if pos == -1: break
            hit_positions[pos] = key
            pos += 1
    sorted_hits = sorted(hit_positions)
    first_match_pos = None
    for i in range(len(sorted_hits) - 1):
        pos_curr = sorted_hits[i]
        pos_next = sorted_hits[i + 1]
        gap_after = pos_next - pos_curr
        if i > 0 and pos_curr - sorted_hits[i - 1] < ITEM_SPACING_MIN:
            continue
        if ITEM_SPACING_MIN <= gap_after <= ITEM_SPACING_MAX:
            first_match_pos = pos_curr
            break
    if first_match_pos is None:
        return None, None
    abs_first = player_offset + first_match_pos
    FF16 = bytes.fromhex('FF' * 16)
    boundary = bytes(data).rfind(FF16, player_offset, abs_first)
    if boundary == -1:
        return None, None
    inventory_start = boundary
    ZERO_RUN = b'\x00' * 0x30
    zero_pos = bytes(data).find(ZERO_RUN, abs_first)
    inventory_end = zero_pos if zero_pos != -1 else len(data)
    return inventory_start, inventory_end

def is_valid_inventory_slot(inv_data, pos, str_len, items_dict, max_chip_id):
    """
    Reject slots where the bytes after the name look like another item string
    (appearance/loadout block) rather than a real inventory slot.
    """
    base         = pos + 4 + str_len
    qty_offset   = base
    chips_offset = base + 4 + 0x23

    if chips_offset + 40 > len(inv_data):
        return False

    # Rule 1: the 4 qty bytes must not be a length prefix pointing to a known item
    qty_val = struct.unpack_from('<I', inv_data, qty_offset)[0]
    if 4 <= qty_val <= 64:
        next_start = qty_offset + 4
        next_end   = next_start + qty_val
        if next_end <= len(inv_data):
            raw = bytes(inv_data[next_start:next_end])
            if raw[-1] == 0x00:
                try:
                    candidate = raw[:-1].decode('ascii')
                    if candidate in items_dict:
                        return False
                except UnicodeDecodeError:
                    pass

    # Rule 2: all 8 chip IDs must be within known range
    for i in range(8):
        cp = chips_offset + i * 5
        if inv_data[cp] > max_chip_id:
            return False

    # Rule 3: no known item key must appear inside the 0x23 skip region
    skip_region = bytes(inv_data[qty_offset + 4: chips_offset])
    for key in items_dict:
        if key.encode('ascii') in skip_region:
            return False

    return True

def parse_inventory(data, inventory_start, inventory_end, items_dict, chips_by_id):
    inv_data = data[inventory_start:inventory_end]
    max_chip_id = max(chips_by_id.keys()) if chips_by_id else 0xFF
    pos = 0
    slots = []

    while pos < len(inv_data) - 4:
        str_len = struct.unpack_from('<I', inv_data, pos)[0]
        if not (4 <= str_len <= 64) or pos + 4 + str_len > len(inv_data):
            pos += 1; continue
        raw = bytes(inv_data[pos + 4: pos + 4 + str_len])
        if raw[-1] != 0x00:
            pos += 1; continue
        try:
            key = raw[:-1].decode('ascii')
        except UnicodeDecodeError:
            pos += 1; continue

        # Must look like a valid item key by prefix — even if not in JSON
        category = item_category(key)
        if category == 'other':
            pos += 1; continue

        # Still need structural validation
        if not is_valid_inventory_slot(inv_data, pos, str_len, items_dict, max_chip_id):
            pos += 1; continue

        base         = pos + 4 + str_len
        qty_offset   = base
        chips_offset = base + 4 + 0x23

        quantity = struct.unpack_from('<I', inv_data, qty_offset)[0]
        chips = []
        for i in range(8):
            cp = chips_offset + i * 5
            chip_id  = inv_data[cp]
            chip_pct = struct.unpack_from('<f', inv_data, cp + 1)[0]
            chips.append({
                'id':     chip_id,
                'name':   chips_by_id.get(chip_id),
                'effect': round(chip_pct * 100, 4),
            })

        in_json = key in items_dict
        slots.append({
            'key':              key,
            'name':             items_dict[key] if in_json else f'[Unknown] {key}',
            'category':         category,
            'quantity':         quantity,
            'chips':            chips,
            'abs_offset':       inventory_start + pos,
            'abs_qty_offset':   inventory_start + qty_offset,
            'abs_chips_offset': inventory_start + chips_offset,
            'str_len':          str_len,
            'unknown':          not in_json,
        })
        pos = chips_offset + 40

    return slots

def write_changes(data, original, edited):
    """
    Write only changed fields into `data` (bytearray).
    Always uses offsets from `original` — edited offsets are stale after a replace.
    """
    for orig, edit in zip(original, edited):
        if orig == edit:
            continue

        # ── Replace item name ─────────────────────────────────────────────────
        if orig['key'] != edit['key']:
            if item_category(orig['key']) != item_category(edit['key']):
                continue
            orig_len   = orig['str_len']            # includes null terminator
            new_bytes  = edit['key'].encode('ascii') + b'\x00'
            # Clamp to original length, pad remainder with nulls
            new_bytes  = new_bytes[:orig_len].ljust(orig_len, b'\x00')
            name_start = orig['abs_offset'] + 4    # skip 4-byte length prefix
            data[name_start: name_start + orig_len] = new_bytes

        # ── Quantity ──────────────────────────────────────────────────────────
        if orig['quantity'] != edit['quantity']:
            struct.pack_into('<I', data, orig['abs_qty_offset'], edit['quantity'])

        # ── Chips ─────────────────────────────────────────────────────────────
        if orig['chips'] != edit['chips']:
            for i, chip in enumerate(edit['chips']):
                orig_chip = orig['chips'][i]
                if orig_chip['id'] == chip['id'] and orig_chip['effect'] == chip['effect']:
                    continue
                cp = orig['abs_chips_offset'] + i * 5
                data[cp] = chip['id']
                struct.pack_into('<f', data, cp + 1, chip['effect'] / 100.0)

# ── App ───────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SAO:FB Inventory Editor')
        self.geometry('1100x680')
        self.minsize(800, 500)
        self.configure(bg='#1a1a24')

        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        self.save_path  = tk.StringVar()
        self.items_path = os.path.join(SCRIPT_DIR, 'items.json')
        self.chips_path = os.path.join(SCRIPT_DIR, 'chips.json')
        self.status_var = tk.StringVar(value='Open a save file to begin.')
        self.dirty      = False

        self.data        = None
        self.items_dict  = {}
        self.chips_by_id = {}
        self.chips_arr   = []
        self.inventory   = []   # working copy
        self.original    = []   # untouched snapshot (offsets live here)
        self.active_cat  = 'all'
        self.selected_item = None

        self._style()
        self._build_ui()

    # ── Style ─────────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        BG = '#1a1a24'; SUR = '#22222e'; SUR2 = '#2a2a38'
        ACC = '#7c6af7'; ACC2 = '#5a4fd4'
        TXT = '#e8e6ff'; MUT = '#8888aa'; BRD = '#3a3a50'; SEL = '#3a3570'

        s.configure('.',              background=BG,   foreground=TXT,  font=('Segoe UI', 10))
        s.configure('TFrame',         background=BG)
        s.configure('TLabel',         background=BG,   foreground=TXT)
        s.configure('Muted.TLabel',   background=BG,   foreground=MUT)
        s.configure('Header.TLabel',  background=BG,   foreground=ACC,  font=('Segoe UI', 11, 'bold'))
        s.configure('Status.TLabel',  background=SUR,  foreground=MUT,  padding=(8, 4))
        s.configure('TNotebook',      background=BG,   borderwidth=0)
        s.configure('TNotebook.Tab',  background=SUR2, foreground=MUT,  padding=(14, 6), font=('Segoe UI', 10))
        s.map('TNotebook.Tab',
              background=[('selected', SUR), ('active', SUR)],
              foreground=[('selected', TXT), ('active', TXT)])
        s.configure('TEntry',         fieldbackground=SUR2, foreground=TXT,
                    insertcolor=TXT,  bordercolor=BRD,  lightcolor=BRD, darkcolor=BRD)
        s.configure('TButton',        background=SUR2, foreground=TXT,
                    bordercolor=BRD,  lightcolor=BRD,  darkcolor=BRD,  padding=(10, 5))
        s.map('TButton',
              background=[('active', SUR), ('pressed', ACC2)],
              foreground=[('pressed', '#fff')])
        s.configure('Accent.TButton', background=ACC,  foreground='#fff',
                    bordercolor=ACC2, lightcolor=ACC,   darkcolor=ACC2, padding=(10, 5))
        s.map('Accent.TButton', background=[('active', ACC2), ('pressed', ACC2)])
        s.configure('TCombobox',      fieldbackground=SUR2, foreground=TXT,
                    background=SUR2,  selectbackground=SEL, selectforeground=TXT,
                    bordercolor=BRD,  arrowcolor=MUT)
        s.map('TCombobox', fieldbackground=[('readonly', SUR2)])
        s.configure('Treeview',       background=SUR,  foreground=TXT,
                    fieldbackground=SUR, bordercolor=BRD, rowheight=24, font=('Segoe UI', 10))
        s.configure('Treeview.Heading', background=SUR2, foreground=MUT,
                    font=('Segoe UI', 9, 'bold'), relief='flat')
        s.map('Treeview',
              background=[('selected', SEL)],
              foreground=[('selected', TXT)])
        s.configure('Vertical.TScrollbar', background=SUR2, troughcolor=BG,
                    arrowcolor=MUT, bordercolor=BG)
        s.configure('Cat.TButton',    background=SUR2, foreground=MUT,
                    bordercolor=BRD,  padding=(8, 4),  font=('Segoe UI', 9))
        s.map('Cat.TButton', background=[('active', SUR), ('pressed', ACC2)])
        s.configure('CatActive.TButton', background=SEL, foreground=TXT,
                    bordercolor=ACC,  padding=(8, 4),  font=('Segoe UI', 9, 'bold'))
        s.configure('TSpinbox',       fieldbackground=SUR2, foreground=TXT,
                    background=SUR2,  bordercolor=BRD,  arrowcolor=MUT, insertcolor=TXT)
        s.configure('TSeparator',     background=BRD)
        self._colors = {'BG': BG, 'SUR': SUR, 'SUR2': SUR2, 'ACC': ACC,
                        'TXT': TXT, 'MUT': MUT, 'BRD': BRD, 'SEL': SEL}

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True)
        self.tab_file = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_file, text='  File  ')
        self._build_file_tab()
        self.tab_inv = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_inv, text='  Inventory  ')
        self._build_inv_tab()
        ttk.Label(self, textvariable=self.status_var,
                  style='Status.TLabel', anchor='w').pack(fill='x', side='bottom')

    # ── File Tab ──────────────────────────────────────────────────────────────
    def _build_file_tab(self):
        C = self._colors
        wrap = ttk.Frame(self.tab_file)
        wrap.pack(padx=40, pady=40, fill='both')

        ttk.Label(wrap, text='SAO:FB Inventory Editor', style='Header.TLabel',
                  font=('Segoe UI', 16, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 24))

        ttk.Label(wrap, text='Save File').grid(row=1, column=0, sticky='w', pady=6, padx=(0, 12))
        ttk.Entry(wrap, textvariable=self.save_path, width=55).grid(row=1, column=1, sticky='ew', pady=6)
        ttk.Button(wrap, text='Browse…', command=self._browse_save).grid(
            row=1, column=2, padx=(8, 0), pady=6)
        ttk.Label(wrap, text='PS4 (.sav) or PC save file — PC files are auto-decrypted.',
                  style='Muted.TLabel', font=('Segoe UI', 9)).grid(
            row=2, column=1, sticky='w', pady=(0, 4))

        wrap.columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(wrap)
        btn_frame.grid(row=7, column=0, columnspan=3, sticky='w', pady=(24, 0))
        ttk.Button(btn_frame, text='Load Inventory', style='Accent.TButton',
                   command=self._load).pack(side='left', padx=(0, 10))
        ttk.Button(btn_frame, text='Save to File',
                   command=self._save).pack(side='left', padx=(0, 10))
        ttk.Button(btn_frame, text='Create Backup',
                   command=self._backup).pack(side='left')

        self.info_box = tk.Text(wrap, height=8, width=60, state='disabled',
                                bg=C['SUR'], fg=C['MUT'], relief='flat',
                                font=('Consolas', 9), bd=0, padx=8, pady=8)
        self.info_box.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(20, 0))

    def _browse_save(self):
        path = filedialog.askopenfilename(filetypes=[('All Files', '*.*')])
        if path:
            self.save_path.set(path)

    def _log(self, msg):
        self.info_box.config(state='normal')
        self.info_box.insert('end', msg + '\n')
        self.info_box.see('end')
        self.info_box.config(state='disabled')

    def _load(self):
        sp = self.save_path.get()
        if not sp or not os.path.exists(sp):
            messagebox.showerror('Error', 'Select a valid save file.'); return
        if not os.path.exists(self.items_path):
            messagebox.showerror('Error', f'items.json not found:\n{self.items_path}'); return
        if not os.path.exists(self.chips_path):
            messagebox.showerror('Error', f'chips.json not found:\n{self.chips_path}'); return
        try:
            with open(self.items_path, 'r', encoding='utf-8') as f:
                self.items_dict = json.load(f)
            with open(self.chips_path, 'r', encoding='utf-8') as f:
                chips_raw = json.load(f)
            self.chips_by_id = {int(v, 16): k for k, v in chips_raw.items()}
            self.chips_arr   = [{'id': 0, 'name': '— empty —'}] + \
                               sorted([{'id': int(v, 16), 'name': k}
                                       for k, v in chips_raw.items()],
                                      key=lambda x: x['id'])

            self.data = open_file(sp)
            if MODE == 'PC':
                self.save_path.set(sp + '.edited')

            inv_start, inv_end = find_inventory_bounds(self.data, self.items_dict)
            if inv_start is None:
                messagebox.showerror('Error', 'Could not locate inventory in save file.'); return

            self.inventory = parse_inventory(self.data, inv_start, inv_end,
                                             self.items_dict, self.chips_by_id)
            self.original  = copy.deepcopy(self.inventory)
            self.dirty     = False

            self._log(f'Mode:        {MODE}')
            self._log(f'Save file:   {sp}')
            self._log(f'Items found: {len(self.inventory)}')
            self._log(f'  Weapons:     {sum(1 for i in self.inventory if i["category"] == "weapon")}')
            self._log(f'  Costumes:    {sum(1 for i in self.inventory if i["category"] == "costume")}')
            self._log(f'  Accessories: {sum(1 for i in self.inventory if i["category"] == "accessory")}')
            self._log(f'  Bullets:     {sum(1 for i in self.inventory if i["category"] == "bullet")}')
            self._log(f'  Materials:   {sum(1 for i in self.inventory if i["category"] == "material")}')
            self._log('─' * 50)

            self._populate_inventory()
            self.notebook.select(self.tab_inv)
            self.status_var.set(f'Loaded {len(self.inventory)} items  [{MODE}]')

        except Exception as e:
            messagebox.showerror('Load Error', str(e))
            raise

    def _save(self):
        if not self.inventory:
            messagebox.showwarning('No Data', 'Load a save file first.'); return
        sp = self.save_path.get()
        if not sp:
            messagebox.showerror('Error', 'No save file path set.'); return
        try:
            # 1. Patch self.data in-memory using original offsets
            write_changes(self.data, self.original, self.inventory)

            # 2. Write raw bytes to disk
            with open(sp, 'wb') as f:
                f.write(self.data)

            # 3. Re-encrypt in place for PC mode
            if MODE == 'PC':
                save_file(sp, self.data)

            # 4. Advance snapshot so next save only diffs new changes
            self.original = copy.deepcopy(self.inventory)
            self.dirty    = False
            self.status_var.set(f'Saved → {sp}')
            self._log(f'Saved to {sp}')
            messagebox.showinfo('Saved', f'File saved successfully.\n{sp}')

        except Exception as e:
            messagebox.showerror('Save Error', str(e))
            raise

    def _backup(self):
        sp = self.save_path.get()
        if not sp or not os.path.exists(sp):
            messagebox.showerror('Error', 'No valid save file loaded.'); return
        bak = sp + '.bak'
        shutil.copy2(sp, bak)
        if MODE == 'PC':
            enc = PC.encrypt_file(bak)
            with open(bak, 'wb') as f:
                f.write(enc)
        messagebox.showinfo('Backup', f'Backup created:\n{bak}')

    # ── Inventory Tab ─────────────────────────────────────────────────────────
    def _build_inv_tab(self):
        f = self.tab_inv
        top = ttk.Frame(f)
        top.pack(fill='x', padx=8, pady=(8, 0))

        self.cat_btns = {}
        for cat in CAT_ORDER:
            b = ttk.Button(top, text=CAT_LABELS[cat],
                           style='CatActive.TButton' if cat == 'all' else 'Cat.TButton',
                           command=lambda c=cat: self._set_cat(c))
            b.pack(side='left', padx=(0, 4))
            self.cat_btns[cat] = b

        ttk.Label(top, text='Search:', style='Muted.TLabel').pack(side='left', padx=(20, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *a: self._filter_list())
        ttk.Entry(top, textvariable=self.search_var, width=22).pack(side='left')
        ttk.Button(top, text='✕', width=2,
                   command=lambda: self.search_var.set('')).pack(side='left', padx=(2, 0))

        pane = ttk.PanedWindow(f, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=8, pady=8)

        left = ttk.Frame(pane)
        pane.add(left, weight=2)
        cols = ('name', 'key', 'info')
        self.tree = ttk.Treeview(left, columns=cols, show='headings', selectmode='browse')
        self.tree.heading('name', text='Name')
        self.tree.heading('key',  text='Key')
        self.tree.heading('info', text='Qty / Chips')
        self.tree.column('name', width=240, minwidth=120)
        self.tree.column('key',  width=160, minwidth=80)
        self.tree.column('info', width=90,  minwidth=60, anchor='center')
        vsb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)
        for cat, color in CAT_COLORS.items():
            self.tree.tag_configure(cat, foreground=color)

        right = ttk.Frame(pane)
        pane.add(right, weight=3)
        self._build_detail_panel(right)

    def _build_detail_panel(self, parent):
        parent.configure(style='TFrame')
        self.detail_frame = parent
        self.detail_placeholder = ttk.Label(parent, text='Select an item to edit.',
                                            style='Muted.TLabel')
        self.detail_placeholder.pack(expand=True)
        self.detail_inner = ttk.Frame(parent)

    # ── Category / filter ─────────────────────────────────────────────────────
    def _set_cat(self, cat):
        self.active_cat = cat
        for c, b in self.cat_btns.items():
            b.configure(style='CatActive.TButton' if c == cat else 'Cat.TButton')
        self._filter_list()

    def _filter_list(self):
        q = self.search_var.get().lower()
        self.tree.delete(*self.tree.get_children())
        # Add unknown tag colour once
        self.tree.tag_configure('unknown', foreground='#888844')

        pool = self.inventory if self.active_cat == 'all' \
               else [i for i in self.inventory if i['category'] == self.active_cat]
        if q:
            pool = [i for i in pool if q in i['name'].lower() or q in i['key'].lower()]
        for item in pool:
            if item['category'] in HAS_CHIPS:
                active = sum(1 for c in item['chips'] if c['id'])
                info = f'{active}/8 chips'
            else:
                info = f'x{item["quantity"]}'
            # Unknown items get a grey-yellow tag instead of their category colour
            tags = ('unknown',) if item.get('unknown') else (item['category'],)
            self.tree.insert('', 'end', iid=str(id(item)),
                             values=(item['name'], item['key'], info),
                             tags=tags)
        self.status_var.set(f'Showing {len(pool)} items')

    def _populate_inventory(self):
        self._filter_list()
        self._clear_detail()

    # ── Selection ─────────────────────────────────────────────────────────────
    def _on_select(self, event):
        sel = self.tree.selection()
        if not sel: return
        item = next((i for i in self.inventory if str(id(i)) == sel[0]), None)
        if item is None: return
        self.selected_item = item
        self._show_detail(item)

    # ── Detail panel ──────────────────────────────────────────────────────────
    def _clear_detail(self):
        self.detail_inner.pack_forget()
        self.detail_placeholder.pack(expand=True)
        self.selected_item = None

    def _show_detail(self, item):
        C = self._colors
        self.detail_placeholder.pack_forget()
        for w in self.detail_inner.winfo_children():
            w.destroy()
        self.detail_inner.pack(fill='both', expand=True, padx=12, pady=8)

        inner = self.detail_inner
        col   = CAT_COLORS.get(item['category'], '#888')

        # Header
        hdr = ttk.Frame(inner)
        hdr.pack(fill='x', pady=(0, 10))
        tk.Label(hdr, text=item['name'], font=('Segoe UI', 13, 'bold'),
                 fg=col, bg=C['BG'], anchor='w').pack(side='left')
        tk.Label(hdr, text=f'  {item["category"].upper()}',
                 font=('Segoe UI', 9), fg=C['MUT'], bg=C['BG']).pack(side='left', pady=(4, 0))
        tk.Label(inner, text=f'Key: {item["key"]}   Offset: {hex(item["abs_offset"])}',
                 font=('Consolas', 9), fg=C['MUT'], bg=C['BG'], anchor='w').pack(fill='x')

        ttk.Separator(inner, orient='horizontal').pack(fill='x', pady=8)

        # ── Replace ───────────────────────────────────────────────────────────
        ttk.Label(inner, text='REPLACE ITEM', style='Header.TLabel').pack(anchor='w')
        ttk.Label(inner, text='Swap with another item of the same category (name only)',
                  style='Muted.TLabel', font=('Segoe UI', 9)).pack(anchor='w', pady=(0, 4))
        same_cat = [i for i in self.inventory if i['category'] == item['category'] and i is not item]
        replace_names = ['— keep current —'] + [f'{i["name"]} ({i["key"]})' for i in same_cat]
        self._replace_var = tk.StringVar(value='— keep current —')
        ttk.Combobox(inner, textvariable=self._replace_var,
                     values=replace_names, state='readonly', width=45).pack(anchor='w', pady=(0, 6))
        ttk.Button(inner, text='Apply Replace',
                   command=lambda: self._apply_replace(item, same_cat)).pack(anchor='w')

        ttk.Separator(inner, orient='horizontal').pack(fill='x', pady=10)

        # ── Quantity ──────────────────────────────────────────────────────────
        ttk.Label(inner, text='QUANTITY', style='Header.TLabel').pack(anchor='w')
        qrow = ttk.Frame(inner)
        qrow.pack(anchor='w', pady=(4, 0))
        self._qty_var = tk.IntVar(value=item['quantity'])
        ttk.Spinbox(qrow, from_=0, to=99999, textvariable=self._qty_var,
                    width=10, font=('Segoe UI', 11)).pack(side='left')
        ttk.Button(qrow, text='Set Quantity',
                   command=lambda: self._apply_qty(item)).pack(side='left', padx=(8, 0))

        # ── Chips ─────────────────────────────────────────────────────────────
        if item['category'] in HAS_CHIPS:
            ttk.Separator(inner, orient='horizontal').pack(fill='x', pady=10)
            ttk.Label(inner, text='CHIP MODS', style='Header.TLabel').pack(anchor='w')
            ttk.Label(inner,
                      text='Only replace/edit existing chips — do not add new ones to empty slots.\n'
                           'If a slot shows a chip name with AGI and 0% effect, leave it alone.',
                      style='Muted.TLabel', font=('Segoe UI', 9)).pack(anchor='w', pady=(0, 6))

            self._chip_vars = []
            chip_names = [c['name'] for c in self.chips_arr]
            grid = ttk.Frame(inner)
            grid.pack(fill='x')

            for i, chip in enumerate(item['chips']):
                row      = i // 2
                col_base = (i % 2) * 4
                px_left  = 0 if col_base == 0 else 16

                tk.Label(grid, text=f'Slot {i + 1}', fg=C['MUT'], bg=C['BG'],
                         font=('Segoe UI', 9, 'bold')).grid(
                    row=row * 2, column=col_base, sticky='w',
                    padx=(px_left, 4), pady=(6, 0))

                id_var  = tk.StringVar(value=chip['name'] or '— empty —')
                eff_var = tk.DoubleVar(value=chip['effect'])
                self._chip_vars.append((id_var, eff_var))

                ttk.Combobox(grid, textvariable=id_var, values=chip_names,
                             state='readonly', width=20).grid(
                    row=row * 2 + 1, column=col_base, sticky='w',
                    padx=(px_left, 0), pady=(0, 2))
                tk.Label(grid, text='%', fg=C['MUT'], bg=C['BG'],
                         font=('Segoe UI', 9)).grid(
                    row=row * 2 + 1, column=col_base + 1, padx=(4, 0), pady=(0, 2))
                ttk.Spinbox(grid, from_=0, to=999, increment=0.5,
                            textvariable=eff_var, width=7, format='%.2f').grid(
                    row=row * 2 + 1, column=col_base + 2, padx=(2, 0), pady=(0, 2))

            ttk.Button(inner, text='Apply Chips', style='Accent.TButton',
                       command=lambda: self._apply_chips(item)).pack(anchor='w', pady=(10, 0))
            ttk.Button(inner, text='Clear All Chips',
                       command=lambda: self._clear_chips(item)).pack(anchor='w', pady=(4, 0))

    # ── Actions ───────────────────────────────────────────────────────────────
    def _apply_replace(self, item, same_cat):
        val = self._replace_var.get()
        if val == '— keep current —': return
        target = next((s for s in same_cat if f'{s["name"]} ({s["key"]})' == val), None)
        if target is None: return
        item['key']     = target['key']
        item['name']    = target['name']
        item['str_len'] = target['str_len']
        self.dirty = True
        self._filter_list()
        self.status_var.set(f'Replaced with {target["name"]}  — unsaved')
        self._show_detail(item)

    def _apply_qty(self, item):
        try:
            item['quantity'] = max(0, int(self._qty_var.get()))
        except Exception:
            return
        self.dirty = True
        self._filter_list()
        self.status_var.set(f'{item["name"]} quantity → {item["quantity"]}  — unsaved')

    def _apply_chips(self, item):
        chip_id_by_name = {c['name']: c['id'] for c in self.chips_arr}
        for i, (id_var, eff_var) in enumerate(self._chip_vars):
            name = id_var.get()
            cid  = chip_id_by_name.get(name, 0)
            try:
                eff = float(eff_var.get())
            except Exception:
                eff = 0.0
            item['chips'][i]['id']     = cid
            item['chips'][i]['name']   = name if cid else None
            item['chips'][i]['effect'] = round(eff, 4)
        self.dirty = True
        self._filter_list()
        active = sum(1 for c in item['chips'] if c['id'])
        self.status_var.set(f'{item["name"]} chips updated ({active} active)  — unsaved')

    def _clear_chips(self, item):
        for chip in item['chips']:
            chip['id']     = 0
            chip['name']   = None
            chip['effect'] = 0.0
        self.dirty = True
        self._show_detail(item)
        self._filter_list()
        self.status_var.set(f'{item["name"]} chips cleared  — unsaved')


if __name__ == '__main__':
    app = App()
    app.mainloop()