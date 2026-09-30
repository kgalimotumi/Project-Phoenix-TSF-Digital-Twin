from __future__ import annotations

import tkinter as tk
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import (ALIGNMENT_LENGTH, CONCRETE_ELEMENTS, PIPE_TYPES, SLEEPERS_PLANNED,
                  SLEEPER_VOLUME, Alignment, Store, cube_status, due_dates,
                  export_rn_picture, photo_metadata, read_survey_csv, scan_encasement_source,
                  working_days, normalize_rn, parse_concrete_lab_pdf)
from lab_merger import merge_ready, preview as preview_lab, write_report

ROOT = Path(__file__).resolve().parent
DB = ROOT / "penstock_tracker.db"
KMZ = ROOT / "assets" / "Penstock Pipes.kmz"
BASELINE_SURVEY = ROOT / "assets" / "Tweefonteine TSF3A-Penstock-As-built Coordinates-2026-09-10.csv"
PICTURE1_DIR = Path(
    r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\15 Quality\6. Inspections\5. Penstock Pipeline\Concrete\Picture 1"
)


def fnum(value, label="Value"):
    try:
        return float(str(value).strip())
    except ValueError:
        raise ValueError(f"{label} must be a number.")


class ProgressBar(tk.Canvas):
    def __init__(self, master, height=26, **kw):
        super().__init__(master, height=height, highlightthickness=0, bg="#1e293b", **kw)
        self.bind("<Configure>", lambda e: self.draw(getattr(self, "value", 0), getattr(self, "label", "")))

    def draw(self, value, label):
        self.value, self.label = max(0, min(100, value)), label
        self.delete("all"); w = max(1, self.winfo_width()); h = max(1, self.winfo_height())
        self.create_rectangle(0, 0, w * self.value / 100, h, fill="#22c55e", width=0)
        self.create_text(w / 2, h / 2, text=label, fill="white", font=("Segoe UI", 10, "bold"))


class ChainagePlot(tk.Canvas):
    def __init__(self, master, store, alignment, height=220, preview_getter=None, on_change=None, **kw):
        super().__init__(master, bg="#0f172a", height=height, highlightthickness=0, **kw); self.store = store; self.alignment=alignment; self.preview_getter=preview_getter; self.on_change=on_change; self.zoom=1.0; self.pan_x=self.pan_y=0; self.drag_origin=None; self.selected_rns=set(); self.selected_concrete_rns=set()
        self.bind("<Configure>", lambda e: self.draw()); self.bind("<MouseWheel>",self._wheel); self.bind("<Button-4>",lambda e:self._zoom_at(e,1.18)); self.bind("<Button-5>",lambda e:self._zoom_at(e,1/1.18)); self.bind("<ButtonPress-2>",self._pan_start); self.bind("<B2-Motion>",self._pan_move); self.bind("<ButtonPress-1>",self._pipe_click); self.bind("<ButtonPress-3>",self._survey_click)

    def _wheel(self,e): self._zoom_at(e,1.18 if e.delta>0 else 1/1.18)
    def _zoom_at(self,e,factor):
        old=self.zoom; self.zoom=max(.65,min(8.0,self.zoom*factor)); ratio=self.zoom/old; self.pan_x=e.x-(e.x-self.pan_x)*ratio; self.pan_y=e.y-(e.y-self.pan_y)*ratio; self.draw()
    def _pan_start(self,e): self.drag_origin=(e.x,e.y,self.pan_x,self.pan_y)
    def _pan_move(self,e):
        if self.drag_origin:
            x,y,px,py=self.drag_origin; self.pan_x=px+e.x-x; self.pan_y=py+e.y-y; self.draw()
    def _component_at(self,e):
        ids=self.find_overlapping(e.x-5,e.y-5,e.x+5,e.y+5)
        for item in reversed(ids):
            for tag in self.gettags(item):
                if tag.startswith("pipe|"): return tag.split("|",1)[1]
        return None
    def _pipe_click(self,e):
        name=self._component_at(e)
        if name: self.store.toggle_kmz_pipe(name,"laid"); self.draw(); self.on_change and self.on_change()
    def _survey_click(self,e):
        name=self._component_at(e)
        if name: self.store.toggle_kmz_pipe(name,"survey_missing"); self.draw(); self.on_change and self.on_change()
    def reset_view(self): self.zoom=1.0; self.pan_x=self.pan_y=0; self.draw()
    def set_selected_rns(self,values):
        self.selected_rns={normalize_rn(x) or str(x).strip().upper() for x in values}
        self.draw()

    def set_selected_concrete_rns(self,values):
        self.selected_concrete_rns={normalize_rn(x) or str(x).strip().upper() for x in values}
        self.draw()

    def draw(self):
        self.delete("all"); w,h=max(400,self.winfo_width()),max(200,self.winfo_height()); margin_x=75; top=36; bottom=h-42
        self.create_text(18,16,anchor="w",text="ACTUAL PENSTOCK PLAN LAYOUT — KMZ ALIGNMENT",fill="white",font=("Segoe UI",13,"bold"))
        ref_lat=sum(p[1] for p in self.alignment.points)/len(self.alignment.points); sx=math.cos(math.radians(ref_lat))
        model=[(p[0]*sx,p[1]) for p in self.alignment.points]; xs=[p[0] for p in model]; ys=[p[1] for p in model]
        dx=max(xs)-min(xs) or 1; dy=max(ys)-min(ys) or 1; scale=min((w-2*margin_x)/dx,(bottom-top)/dy)
        scale*=self.zoom; ox=(w-(max(xs)+min(xs))*scale)/2+self.pan_x; oy=(top+bottom+(max(ys)+min(ys))*scale)/2+self.pan_y
        def cv(points):
            out=[]
            for lon,lat in points: out.extend((lon*sx*scale+ox,oy-lat*scale))
            return out
        full=cv(self.alignment.points); self.create_line(*full,fill="#334155",width=max(5,int(17*self.zoom)),smooth=True,splinesteps=8)
        status=self.store.kmz_status()
        for component in self.alignment.components:
            row=status.get(component["name"]); laid=bool(row and row["laid"]); missing=bool(row and row["survey_missing"])
            colour="#ef4444" if missing else ("#38bdf8" if laid else "#64748b")
            pts=cv(component["points"]); self.create_line(*pts,fill=colour,width=max(4,int((12 if laid else 8)*self.zoom)),smooth=True,splinesteps=8,tags=(f"pipe|{component['name']}",))
            if self.zoom>=1.8:
                centre=component["points"][len(component["points"])//2]; cx,cy=cv([centre]); self.create_text(cx,cy-12,text=component["name"],fill="white",font=("Segoe UI",8,"bold"),tags=(f"pipe|{component['name']}",))
        concrete_rows=[
            row for row in self.store.rows("concrete_pours")
            if row["start_ch"] is not None and row["end_ch"] is not None
            and "encasement" in str(row["element"] or "").lower()
        ]
        def _concrete_selected(row):
            rr=normalize_rn(row["rn"]) or str(row["rn"] or "").strip().upper()
            return rr in self.selected_concrete_rns

        # Non-selected concrete first.
        for row in [r for r in concrete_rows if not _concrete_selected(r)]:
            pts=cv(self.alignment.path_between(row["start_ch"],row["end_ch"]))
            self.create_line(*pts,fill="#f59e0b",width=7,smooth=True,splinesteps=8)

        # Selected RN sections LAST, with outline + bright core so every separate
        # chainage range remains visible even where other records overlap.
        for row in [r for r in concrete_rows if _concrete_selected(r)]:
            row_rn=normalize_rn(row["rn"]) or str(row["rn"] or "").strip().upper()
            pts=cv(self.alignment.path_between(row["start_ch"],row["end_ch"]))
            self.create_line(*pts,fill="#111827",width=21,smooth=True,splinesteps=8)
            self.create_line(*pts,fill="#fde047",width=15,smooth=True,splinesteps=8)
            mid=pts[len(pts)//2]
            label=f"{row_rn}  CH{float(row['start_ch']):.1f}–CH{float(row['end_ch']):.1f}"
            self.create_text(
                mid[0],mid[1]-24,text=label,fill="white",
                font=("Segoe UI",10,"bold")
            )
        for row in self.store.rows("pipe_installations"):
            if str(row["rn"] or "").strip().upper() not in self.selected_rns: continue
            pts=cv(self.alignment.path_between(row["start_ch"],row["end_ch"])); self.create_line(*pts,fill="#fde047",width=6,smooth=True,splinesteps=8)
            x,y=pts[len(pts)//2]; self.create_text(x,y-13,text=row["rn"],fill="#fde047",font=("Segoe UI",9,"bold"))
        if self.preview_getter:
            try:
                a,b=self.preview_getter(); pts=cv(self.alignment.path_between(float(a),float(b))); self.create_line(*pts,fill="#fde047",width=4,dash=(8,5),smooth=True,splinesteps=8)
            except Exception: pass
        for ch,label,anchor,dx_label,dy_label in ((0,"CH0\nFinal TEE 2","w",8,0),(162,"CH162\n119° bend","center",0,-28),(244,"CH244\n80° bend","e",-8,0)):
            x,y=cv([self.alignment.point_at_chainage(ch)]); self.create_oval(x-5,y-5,x+5,y+5,fill="#e2e8f0",outline="#0f172a"); self.create_text(x+dx_label,y+dy_label,text=label,anchor=anchor,fill="#e2e8f0",font=("Segoe UI",9,"bold"),justify="center")
        lx=18; ly=h-18
        for colour,text in (("#64748b","Not laid"),("#38bdf8","Pipe laid + surveyed"),("#ef4444","As-built outstanding"),("#f59e0b","Concrete encasement")):
            self.create_line(lx,ly,lx+25,ly,fill=colour,width=6); self.create_text(lx+31,ly,text=text,anchor="w",fill="#cbd5e1",font=("Segoe UI",9)); lx+=145
        self.create_text(w-18,16,anchor="e",text="Wheel: zoom  |  Middle-drag: pan  |  Left-click: laid ON/OFF  |  Right-click: survey flag",fill="#94a3b8",font=("Segoe UI",9))


class StrengthPlot(tk.Canvas):
    def __init__(self, master, store, pour_var, **kw):
        super().__init__(master, bg="#0f172a", height=290, highlightthickness=0, **kw)
        self.store, self.pour_var = store, pour_var
        self.bind("<Configure>", lambda e: self.draw())

    def draw(self):
        self.delete("all"); w,h=self.winfo_width(),self.winfo_height(); left,right,top,bottom=65,max(100,w-35),38,max(80,h-52)
        try: pid=int(self.pour_var.get())
        except (ValueError,TypeError):
            self.create_text(w/2,h/2,text="Enter or select a Pour ID to plot concrete strength.",fill="#94a3b8",font=("Segoe UI",12)); return
        pour,results=self.store.strength_series(pid)
        if not pour:
            self.create_text(w/2,h/2,text="Pour ID not found.",fill="#f87171",font=("Segoe UI",12)); return
        target=float(pour["target_mpa"]); max_result=max([float(r["result_mpa"]) for r in results] or [target]); ymax=max(5.0,target*1.25,max_result*1.15)
        def xy(age,mpa): return left+(right-left)*age/28.0, bottom-(bottom-top)*mpa/ymax
        self.create_text(left,16,anchor="w",text=f"POUR #{pid} — {pour['element']} | Target {target:.1f} MPa",fill="white",font=("Segoe UI",12,"bold"))
        self.create_line(left,top,left,bottom,right,bottom,fill="#64748b",width=2)
        for age in (0,7,14,21,28):
            x,_=xy(age,0); self.create_line(x,bottom,x,bottom+5,fill="#94a3b8"); self.create_text(x,bottom+18,text=str(age),fill="#cbd5e1")
        for m in range(0,int(ymax)+1,max(1,int(ymax/5))):
            _,y=xy(0,m); self.create_line(left-5,y,right,y,fill="#1e293b"); self.create_text(left-10,y,text=str(m),anchor="e",fill="#94a3b8")
        self.create_text((left+right)/2,h-8,text="Concrete age (days)",fill="#cbd5e1"); self.create_text(15,(top+bottom)/2,text="MPa",angle=90,fill="#cbd5e1")
        # Contractual acceptance markers: 60% at 7 days and 100% at 28 days.
        tx1,ty1=xy(7,target*0.60); tx2,ty2=xy(28,target)
        self.create_line(tx1,ty1,tx2,ty2,fill="#f59e0b",width=2,dash=(7,4))
        self.create_oval(tx1-5,ty1-5,tx1+5,ty1+5,fill="#f59e0b",outline=""); self.create_oval(tx2-5,ty2-5,tx2+5,ty2+5,fill="#f59e0b",outline="")
        self.create_text(tx1,ty1-14,text=f"7d ≥ {target*.6:.1f}",fill="#fbbf24"); self.create_text(tx2-5,ty2-14,text=f"28d ≥ {target:.1f}",anchor="e",fill="#fbbf24")
        points=[]
        for r in results:
            x,y=xy(float(r["age_days"]),float(r["result_mpa"])); points.extend((x,y))
        if len(points)>=4: self.create_line(*points,fill="#38bdf8",width=3)
        for r in results:
            age,val=float(r["age_days"]),float(r["result_mpa"]); x,y=xy(age,val)
            req=target*.60 if age==7 else (target if age==28 else None); colour="#22c55e" if req is None or val>=req else "#ef4444"
            self.create_oval(x-6,y-6,x+6,y+6,fill=colour,outline="white"); self.create_text(x,y-15,text=f"{val:.1f}",fill="white",font=("Segoe UI",9,"bold"))
        if not results: self.create_text((left+right)/2,(top+bottom)/2,text="No cube results recorded for this pour yet.",fill="#94a3b8")


class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title("Tweefontein Penstock League Tracker — RN Concrete Events v2.5.2"); self.geometry("1420x860"); self.minsize(1100,720)
        self.configure(bg="#0f172a"); self.store=Store(DB); self.alignment=Alignment.from_kmz(KMZ); self.store.sync_kmz_pipes(self.alignment.components)
        self.start_photo=self.end_photo=""; self.survey_path=""; self.enc_start_photo=self.enc_end_photo=""; self.edit_pipe_id=None
        self._style(); self._build(); self.refresh_all()

    def _style(self):
        s=ttk.Style(self); s.theme_use("clam"); s.configure("TFrame",background="#0f172a"); s.configure("Card.TFrame",background="#1e293b")
        s.configure("TLabel",background="#0f172a",foreground="#e2e8f0",font=("Segoe UI",11)); s.configure("Card.TLabel",background="#1e293b",foreground="#e2e8f0",font=("Segoe UI",11))
        s.configure("Title.TLabel",font=("Segoe UI",22,"bold"),foreground="white"); s.configure("Big.TLabel",font=("Segoe UI",19,"bold"),foreground="#38bdf8")
        s.configure("TButton",font=("Segoe UI",11,"bold"),padding=9); s.configure("TNotebook",background="#0f172a",borderwidth=0); s.configure("TNotebook.Tab",font=("Segoe UI",11,"bold"),padding=(20,11))
        s.configure("TEntry",font=("Segoe UI",11),padding=4); s.configure("TCombobox",font=("Segoe UI",11),padding=3)
        s.configure("Treeview",rowheight=32,background="#1e293b",fieldbackground="#1e293b",foreground="white",font=("Segoe UI",10)); s.configure("Treeview.Heading",font=("Segoe UI",10,"bold"))

    def _build(self):
        head=ttk.Frame(self); head.pack(fill="x",padx=22,pady=(16,8)); ttk.Label(head,text="PENSTOCK LEAGUE TRACKER",style="Title.TLabel").pack(side="left")
        ttk.Label(head,text="CH0 Final Penstock TEE 2  →  CH244 80° Bend").pack(side="right")
        self.tabs=ttk.Notebook(self); self.tabs.pack(fill="both",expand=True,padx=18,pady=(0,18))
        self.dashboard=ttk.Frame(self.tabs); self.pipe=ttk.Frame(self.tabs); self.conc=ttk.Frame(self.tabs); self.results=ttk.Frame(self.tabs); self.merger=ttk.Frame(self.tabs); self.league=ttk.Frame(self.tabs)
        for f,n in ((self.dashboard,"Dashboard"),(self.pipe,"Pipe Installation"),(self.conc,"Concrete Encasement"),(self.results,"Cube Results"),(self.merger,"Lab PDF Merger"),(self.league,"League")): self.tabs.add(f,text=n)
        self._dashboard(); self._pipe(); self._concrete(); self._results(); self._merger(); self._league()

    def _card(self,parent,title,col):
        f=ttk.Frame(parent,style="Card.TFrame",padding=16); f.grid(row=0,column=col,sticky="nsew",padx=7); ttk.Label(f,text=title,style="Card.TLabel").pack(anchor="w"); v=ttk.Label(f,text="—",style="Big.TLabel"); v.pack(anchor="w",pady=8); b=ProgressBar(f); b.pack(fill="x"); return v,b

    def _dashboard(self):
        cards=ttk.Frame(self.dashboard); cards.pack(fill="x",padx=14,pady=14)
        for i in range(4): cards.columnconfigure(i,weight=1)
        self.pipe_metric,self.pipe_bar=self._card(cards,"PIPE LAID",0); self.conc_metric,self.conc_bar=self._card(cards,"CONCRETE CAST",1); self.sleep_metric,self.sleep_bar=self._card(cards,"SLEEPERS CAST",2); self.alert_metric,self.alert_bar=self._card(cards,"CUBE ACTIONS DUE",3)
        plan=ttk.Frame(self.dashboard,style="Card.TFrame",padding=14); plan.pack(fill="x",padx=22,pady=(0,8))
        controls=ttk.Frame(plan,style="Card.TFrame"); controls.pack(fill="x")
        ttk.Label(controls,text="PENSTOCK PLAN vs ACTUAL",style="Card.TLabel",font=("Segoe UI",14,"bold")).pack(side="left",padx=(0,20))
        ttk.Label(controls,text="Measure",style="Card.TLabel").pack(side="left"); self.plan_scope=tk.StringVar(value=self.store.get_setting("plan_scope","Pipe laying")); scope=ttk.Combobox(controls,textvariable=self.plan_scope,values=("Pipe laying","Concrete works","Sleepers"),state="readonly",width=18); scope.pack(side="left",padx=(6,16)); scope.bind("<<ComboboxSelected>>",lambda e:self.refresh_target_plan())
        ttk.Label(controls,text="Target date",style="Card.TLabel").pack(side="left"); self.plan_target=tk.StringVar(value=self.store.get_setting("plan_target","2026-09-23")); ttk.Entry(controls,textvariable=self.plan_target,width=13).pack(side="left",padx=(6,10)); ttk.Button(controls,text="CALCULATE",command=self.refresh_target_plan).pack(side="left")
        metrics=ttk.Frame(plan,style="Card.TFrame"); metrics.pack(fill="x",pady=(12,0));
        self.plan_values=[]
        for i,title in enumerate(("PLANNED %","ACTUAL %","VARIANCE","PLAN ATTAINMENT","REQUIRED / WORKDAY")):
            f=ttk.Frame(metrics,style="Card.TFrame"); f.pack(side="left",fill="x",expand=True,padx=5); ttk.Label(f,text=title,style="Card.TLabel").pack(anchor="center"); v=ttk.Label(f,text="—",style="Big.TLabel"); v.pack(anchor="center"); self.plan_values.append(v)
        self.plan_context=ttk.Label(plan,text="",style="Card.TLabel"); self.plan_context.pack(anchor="center",pady=(8,0))
        self.plot=ChainagePlot(self.dashboard,self.store,self.alignment,on_change=self.refresh_all); self.plot.pack(fill="x",padx=22,pady=8)
        box=ttk.Frame(self.dashboard,style="Card.TFrame",padding=14); box.pack(fill="both",expand=True,padx=22,pady=8)
        ttk.Label(box,text="REMAINING PIPE INVENTORY",style="Card.TLabel",font=("Segoe UI",12,"bold")).pack(anchor="w")
        self.inventory=ttk.Treeview(box,columns=("planned","installed","remaining","length"),show="tree headings",height=8); self.inventory.heading("#0",text="Pipe type")
        for c,t in (("planned","Planned no."),("installed","Installed no."),("remaining","Remaining no."),("length","Piece length")): self.inventory.heading(c,text=t); self.inventory.column(c,width=120,anchor="center")
        self.inventory.column("#0",width=300); self.inventory.pack(fill="both",expand=True,pady=(8,0))

    def _form_row(self,parent,row,label,var,width=22):
        ttk.Label(parent,text=label,style="Card.TLabel").grid(row=row,column=0,sticky="w",pady=5); e=ttk.Entry(parent,textvariable=var,width=width); e.grid(row=row,column=1,sticky="ew",pady=5,padx=(10,0)); return e

    def _pipe(self):
        left=ttk.Frame(self.pipe,style="Card.TFrame",padding=18); left.pack(side="left",fill="y",padx=18,pady=18); right=ttk.Frame(self.pipe,style="Card.TFrame",padding=12); right.pack(side="left",fill="both",expand=True,padx=(0,18),pady=18)
        ttk.Label(left,text="RECORD PIPE INSTALLATION",style="Card.TLabel",font=("Segoe UI",14,"bold")).grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,10))
        self.p_team=tk.StringVar(value="Masha & Martin"); self.p_date=tk.StringVar(value=date.today().isoformat()); self.p_rn=tk.StringVar(); self.p_type=tk.StringVar(value=next(iter(PIPE_TYPES))); self.p_qty=tk.StringVar(value="1"); self.p_start=tk.StringVar(value="0"); self.p_end=tk.StringVar(); self.p_len=tk.StringVar(); self.p_note=tk.StringVar(); self.p_source=tk.StringVar(value="Manual chainage"); self.p_laid=tk.BooleanVar(value=True); self.p_survey_missing=tk.BooleanVar(value=False)
        r=1
        for label,var in (("Team",self.p_team),("Date",self.p_date),("RN (optional)",self.p_rn)):
            self._form_row(left,r,label,var); r+=1
        ttk.Label(left,text="Pipe type",style="Card.TLabel").grid(row=r,column=0,sticky="w"); ttk.Combobox(left,textvariable=self.p_type,values=list(PIPE_TYPES),state="readonly",width=25).grid(row=r,column=1,sticky="ew",padx=(10,0)); r+=1
        for label,var in (("Quantity",self.p_qty),("Start chainage",self.p_start),("End chainage",self.p_end),("Installed length (m)",self.p_len),("Notes",self.p_note)):
            self._form_row(left,r,label,var); r+=1
        ttk.Label(left,text="Evidence source",style="Card.TLabel").grid(row=r,column=0,sticky="w",pady=5); ttk.Combobox(left,textvariable=self.p_source,values=("Manual chainage","Survey CSV","Timestamp photos"),state="readonly").grid(row=r,column=1,sticky="ew",padx=(10,0)); r+=1
        ttk.Checkbutton(left,text="Pipe laid — include length in progress",variable=self.p_laid).grid(row=r,column=0,columnspan=2,sticky="w",pady=(8,2)); r+=1
        ttk.Checkbutton(left,text="FLAG: as-built survey outstanding",variable=self.p_survey_missing).grid(row=r,column=0,columnspan=2,sticky="w",pady=(2,8)); r+=1
        ttk.Button(left,text="Load survey CSV",command=self.load_pipe_survey).grid(row=r,column=0,sticky="ew",pady=5); ttk.Button(left,text="Load start/end photos",command=self.load_pipe_photos).grid(row=r,column=1,sticky="ew",padx=(10,0)); r+=1
        ttk.Button(left,text="Load supplied 10-Sep as-built",command=lambda:self.load_pipe_survey(str(BASELINE_SURVEY))).grid(row=r,column=0,columnspan=2,sticky="ew",pady=5); r+=1
        ttk.Button(left,text="SAVE NEW INSTALLATION",command=self.save_pipe).grid(row=r,column=0,columnspan=2,sticky="ew",pady=(14,4)); r+=1
        ttk.Button(left,text="UPDATE SELECTED RECORD",command=self.update_selected_pipe).grid(row=r,column=0,columnspan=2,sticky="ew",pady=4); r+=1
        ttk.Button(left,text="SWITCH SELECTED PIPE ON / OFF",command=self.toggle_selected_pipe).grid(row=r,column=0,columnspan=2,sticky="ew",pady=4); r+=1
        ttk.Button(left,text="DELETE SELECTED",command=self.delete_selected_pipe).grid(row=r,column=0,columnspan=2,sticky="ew",pady=4)
        ttk.Label(right,text="PIPELINE LAYOUT & INSTALLATION LOG",style="Card.TLabel",font=("Segoe UI",14,"bold")).pack(anchor="w")
        tools=ttk.Frame(right,style="Card.TFrame"); tools.pack(fill="x"); ttk.Label(tools,text="Click individual KMZ pipes to update progress",style="Card.TLabel").pack(side="left"); ttk.Button(tools,text="RESET ZOOM",command=lambda:self.pipe_plot.reset_view()).pack(side="right")
        self.pipe_plot=ChainagePlot(right,self.store,self.alignment,height=320,preview_getter=lambda:(self.p_start.get(),self.p_end.get()),on_change=self.refresh_all); self.pipe_plot.pack(fill="x",pady=(8,12))
        self.pipe_tree=ttk.Treeview(right,columns=("date","team","ch","length","type","rn","status","survey"),show="headings")
        for c,t,w in (("date","Date",90),("team","Team",120),("ch","Chainage",135),("length","Length m",80),("type","Pipe type",150),("rn","RN",75),("status","Pipe laying",90),("survey","As-built",100)): self.pipe_tree.heading(c,text=t); self.pipe_tree.column(c,width=w,anchor="center")
        self.pipe_tree.tag_configure("off",foreground="#94a3b8"); self.pipe_tree.tag_configure("missing",foreground="#f87171")
        self.pipe_tree.pack(fill="both",expand=True,pady=8); self.pipe_tree.bind("<<TreeviewSelect>>",self.select_pipe_record)

    def rn_picture_dialog(self):
        # Picture 1 is driven by CONCRETE ENCASEMENT chainages, not pipe-lay RNs.
        rows=[
            r for r in self.store.rows("concrete_pours","rn")
            if str(r["rn"] or "").strip()
            and r["start_ch"] is not None and r["end_ch"] is not None
            and "encasement" in str(r["element"] or "").lower()
        ]
        rns=sorted(
            {normalize_rn(r["rn"]) or str(r["rn"]).strip().upper() for r in rows},
            key=lambda x:(int(''.join(c for c in x if c.isdigit()) or 0),x)
        )
        if not rns:
            messagebox.showerror(
                "RN Picture 1",
                "No concrete encasements with both an RN and chainages were found."
            )
            return

        win=tk.Toplevel(self)
        win.title("Concrete Encasement — Checklist Picture 1")
        win.geometry("610x650")
        win.configure(bg="#0f172a")
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,text="CONCRETE ENCASEMENT — PICTURE 1",
            style="Title.TLabel"
        ).pack(anchor="w",padx=18,pady=(16,5))
        ttk.Label(
            win,
            text="Select one RN or several RNs. Every encasement chainage under the selected RN is highlighted on the Penstock sketch."
        ).pack(anchor="w",padx=18,pady=(0,8))

        box=tk.Listbox(
            win,selectmode="extended",bg="#1e293b",fg="white",
            selectbackground="#0284c7",font=("Segoe UI",12),height=15,
            exportselection=False
        )
        for rn in rns:
            count=sum(
                1 for r in rows
                if (normalize_rn(r["rn"]) or str(r["rn"]).strip().upper())==rn
            )
            box.insert("end",f"{rn}   ({count} encasement{'s' if count != 1 else ''})")
        box.pack(fill="both",expand=True,padx=18,pady=8)

        def selected():
            return [rns[i] for i in box.curselection()]

        def preview(*_):
            chosen=selected()
            # Both main sketches highlight the concrete RN selection.
            self.pipe_plot.set_selected_concrete_rns(chosen)
            self.plot.set_selected_concrete_rns(chosen)

        box.bind("<<ListboxSelect>>",preview)

        controls=ttk.Frame(win)
        controls.pack(fill="x",padx=18,pady=(0,8))
        ttk.Button(
            controls,text="SELECT ALL ENCASEMENTS",
            command=lambda:(box.select_set(0,"end"),preview())
        ).pack(side="left")
        ttk.Button(
            controls,text="CLEAR ALL",
            command=lambda:(box.selection_clear(0,"end"),preview())
        ).pack(side="left",padx=8)

        destination=ttk.Label(
            win,
            text=f"Auto-save folder:\n{PICTURE1_DIR}\nFilename: selected RN.png",
            justify="left"
        )
        destination.pack(anchor="w",padx=18,pady=(2,10))

        def save_picture():
            chosen=selected()
            if not chosen:
                messagebox.showerror("RN Picture 1","Select at least one RN.",parent=win)
                return
            try:
                PICTURE1_DIR.mkdir(parents=True,exist_ok=True)

                saved=[]
                # Checklist convention: one Picture 1 per RN, named exactly RN###.png.
                for rn in chosen:
                    target=PICTURE1_DIR / f"{rn}.png"
                    if target.exists():
                        replace=messagebox.askyesno(
                            "Replace Picture 1?",
                            f"{target.name} already exists.\n\nReplace it?",
                            parent=win
                        )
                        if not replace:
                            continue

                    export_rn_picture(
                        target,
                        self.alignment,
                        self.store.rows("kmz_pipes","start_ch"),
                        rows,
                        [rn],
                        source_kind="concrete",
                    )
                    saved.append(str(target))

                if saved:
                    messagebox.showinfo(
                        "Picture 1 saved",
                        "Checklist Picture 1 saved:\n\n" + "\n".join(saved),
                        parent=win
                    )
            except Exception as e:
                messagebox.showerror("Cannot save Picture 1",str(e),parent=win)

        ttk.Button(
            win,text="SAVE SELECTED RN PICTURE 1",
            command=save_picture
        ).pack(fill="x",padx=18,pady=(0,16))

        def close():
            self.pipe_plot.set_selected_concrete_rns(set())
            self.plot.set_selected_concrete_rns(set())
            win.destroy()
        win.protocol("WM_DELETE_WINDOW",close)

    def load_pipe_survey(self,p=None):
        p=p or filedialog.askopenfilename(filetypes=[("Survey CSV","*.csv")]);
        if not p:return
        try:
            d=read_survey_csv(p,self.alignment); self.p_start.set(d["start_ch"]); self.p_end.set(d["end_ch"]); self.p_len.set(round(d["end_ch"]-d["start_ch"],2)); self.p_source.set("Survey CSV"); self.survey_path=p
            if not self.p_qty.get().strip(): self.p_qty.set("1")
            detail="\n".join(f"{x['point']}: CH{x['chainage']:.2f}, offset {x['offset']:.2f} m, RL {x['elevation']:.3f}" for x in d.get("details",[]))
            self.pipe_plot.draw()
            messagebox.showinfo("Survey loaded",f"{d['coordinate_system']}\n{d['points']} point(s): CH{d['start_ch']:.2f} to CH{d['end_ch']:.2f}\nMaximum alignment offset: {d.get('max_offset',0):.2f} m"+(f"\n\n{detail}" if detail else ""))
        except Exception as e: messagebox.showerror("Survey error",str(e))

    def _photo_pair(self):
        paths=filedialog.askopenfilenames(title="Select start and end timestamp photos",filetypes=[("Photos","*.jpg *.jpeg *.png")]);
        if len(paths)!=2: raise ValueError("Select exactly two photos: start and end.")
        data=[]
        for p in paths:
            m=photo_metadata(p)
            if "chainage" in m:
                m.setdefault("offset",None)
            elif "lat" in m:
                ch,offset=self.alignment.chainage(m["lon"],m["lat"]); m.update(chainage=ch,offset=offset,chainage_source="GPS")
            else:
                raise ValueError(f"No GPS metadata or CH chainage in filename: {Path(p).name}. Rename it like CH164.2.jpg or use manual chainage.")
            if not (0<=m["chainage"]<=244): raise ValueError(f"Chainage in {Path(p).name} must be between CH0 and CH244.")
            data.append(m)
        data.sort(key=lambda x:x["chainage"]); return data

    def load_pipe_photos(self):
        try:
            d=self._photo_pair(); self.start_photo,self.end_photo=d[0]["path"],d[1]["path"]; self.p_start.set(d[0]["chainage"]); self.p_end.set(d[1]["chainage"]); self.p_len.set(round(d[1]["chainage"]-d[0]["chainage"],2)); self.p_source.set("Timestamp photos")
            sources=f"Sources: {d[0].get('chainage_source','photo')} / {d[1].get('chainage_source','photo')}"
            offsets="" if d[0].get("offset") is None or d[1].get("offset") is None else f"\nAlignment offsets: {d[0]['offset']:.1f} m / {d[1]['offset']:.1f} m"
            messagebox.showinfo("Photos located",f"CH{d[0]['chainage']:.2f} to CH{d[1]['chainage']:.2f}\n{sources}{offsets}")
        except Exception as e: messagebox.showerror("Photo error",str(e))

    def save_pipe(self):
        try:
            start,end=fnum(self.p_start.get(),"Start chainage"),fnum(self.p_end.get(),"End chainage"); length=fnum(self.p_len.get() or abs(end-start),"Length")
            if not (0<=start<=244 and 0<=end<=244 and length>0): raise ValueError("Chainages must be CH0–CH244 and length must be positive.")
            q=int(self.p_qty.get().strip() or "1"); self.p_qty.set(str(q)); evidence=self.survey_path if self.p_source.get()=="Survey CSV" else " | ".join(x for x in (self.start_photo,self.end_photo) if x)
            self.store.add_pipe(team=self.p_team.get().strip(),installed_date=self.p_date.get(),start_ch=min(start,end),end_ch=max(start,end),length_m=length,pipe_type=self.p_type.get(),quantity=q,rn=self.p_rn.get().strip(),evidence_type=self.p_source.get(),evidence_path=evidence,notes=self.p_note.get().strip(),laid=int(self.p_laid.get()),survey_missing=int(self.p_survey_missing.get()))
            self.refresh_all(); messagebox.showinfo("Saved","Pipe installation recorded.")
        except Exception as e: messagebox.showerror("Cannot save",str(e))

    def _pipe_form_data(self):
        start,end=fnum(self.p_start.get(),"Start chainage"),fnum(self.p_end.get(),"End chainage"); length=fnum(self.p_len.get() or abs(end-start),"Length")
        if not (0<=start<=244 and 0<=end<=244 and length>0): raise ValueError("Chainages must be CH0–CH244 and length must be positive.")
        q=int(self.p_qty.get().strip() or "1"); self.p_qty.set(str(q))
        evidence=self.survey_path if self.p_source.get()=="Survey CSV" else " | ".join(x for x in (self.start_photo,self.end_photo) if x)
        return dict(team=self.p_team.get().strip(),installed_date=self.p_date.get(),start_ch=min(start,end),end_ch=max(start,end),length_m=length,pipe_type=self.p_type.get(),quantity=q,rn=self.p_rn.get().strip(),evidence_type=self.p_source.get(),evidence_path=evidence,notes=self.p_note.get().strip(),laid=int(self.p_laid.get()),survey_missing=int(self.p_survey_missing.get()))

    def select_pipe_record(self,*_):
        selected=self.pipe_tree.selection()
        if not selected:return
        self.edit_pipe_id=int(selected[0]); r=self.store.db.execute("SELECT * FROM pipe_installations WHERE id=?",(self.edit_pipe_id,)).fetchone()
        if not r:return
        self.p_team.set(r["team"]); self.p_date.set(r["installed_date"]); self.p_rn.set(r["rn"] or ""); self.p_type.set(r["pipe_type"]); self.p_qty.set(str(r["quantity"] or 1)); self.p_start.set(r["start_ch"]); self.p_end.set(r["end_ch"]); self.p_len.set(r["length_m"]); self.p_note.set(r["notes"] or ""); self.p_source.set(r["evidence_type"] or "Manual chainage"); self.p_laid.set(bool(r["laid"])); self.p_survey_missing.set(bool(r["survey_missing"])); self.survey_path=r["evidence_path"] or ""

    def update_selected_pipe(self):
        try:
            if not self.edit_pipe_id: raise ValueError("Select the old installation row in the log first.")
            data=self._pipe_form_data(); self.store.update_pipe(self.edit_pipe_id,**data); self.refresh_all(); messagebox.showinfo("Updated",f"Installation record #{self.edit_pipe_id} was replaced with the current form values.")
        except Exception as e: messagebox.showerror("Cannot update",str(e))

    def toggle_selected_pipe(self):
        if not self.edit_pipe_id: messagebox.showerror("Cannot switch","Select a pipe row first."); return
        record_id=self.edit_pipe_id; self.store.toggle_pipe_laid(record_id); self.refresh_all(); self.pipe_tree.selection_set(str(record_id)); self.pipe_tree.focus(str(record_id)); self.select_pipe_record()

    def delete_selected_pipe(self):
        if not self.edit_pipe_id: messagebox.showerror("Cannot delete","Select an installation row first."); return
        if messagebox.askyesno("Delete installation",f"Delete installation record #{self.edit_pipe_id}?"):
            self.store.delete_pipe(self.edit_pipe_id); self.edit_pipe_id=None; self.refresh_all()

    def _concrete(self):
        self.concrete_batch=[]
        # User-resizable Concrete tab: drag the vertical divider to resize the form/sketch area.
        self.conc_main_pane=tk.PanedWindow(
            self.conc,orient="horizontal",sashwidth=8,sashrelief="raised",
            bg="#334155",bd=0
        )
        self.conc_main_pane.pack(fill="both",expand=True,padx=14,pady=14)
        left=ttk.Frame(self.conc_main_pane,style="Card.TFrame",padding=16)
        right=ttk.Frame(self.conc_main_pane,style="Card.TFrame",padding=8)
        self.conc_main_pane.add(left,minsize=285,width=320,stretch="never")
        self.conc_main_pane.add(right,minsize=650,stretch="always")
        ttk.Label(left,text="CONCRETE EVENT / RN",style="Card.TLabel",font=("Segoe UI",14,"bold")).grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,10))
        self.c_team=tk.StringVar(value="Concrete Team"); self.c_date=tk.StringVar(value=date.today().isoformat()); self.c_element=tk.StringVar(value="Pipe encasement"); self.c_start=tk.StringVar(); self.c_end=tk.StringVar(); self.c_vol=tk.StringVar(); self.c_qty=tk.StringVar(value="1"); self.c_rn=tk.StringVar(); self.c_target=tk.StringVar(value="15"); self.c_note=tk.StringVar()
        r=1
        for label,var in (("Team",self.c_team),("Date cast",self.c_date),("RN (shared)",self.c_rn),("Target strength MPa",self.c_target)):
            self._form_row(left,r,label,var); r+=1
        ttk.Separator(left,orient="horizontal").grid(row=r,column=0,columnspan=2,sticky="ew",pady=8); r+=1
        ttk.Label(left,text="ADD STRUCTURE TO EVENT",style="Card.TLabel",font=("Segoe UI",11,"bold")).grid(row=r,column=0,columnspan=2,sticky="w",pady=(0,6)); r+=1
        ttk.Label(left,text="Element",style="Card.TLabel").grid(row=r,column=0,sticky="w"); cb=ttk.Combobox(left,textvariable=self.c_element,values=list(CONCRETE_ELEMENTS),state="readonly"); cb.grid(row=r,column=1,sticky="ew",padx=(10,0)); cb.bind("<<ComboboxSelected>>",self.element_changed); r+=1
        for label,var in (("Start chainage",self.c_start),("End chainage",self.c_end),("Volume cast m³",self.c_vol),("Quantity",self.c_qty),("Notes",self.c_note)):
            self._form_row(left,r,label,var); r+=1
        ttk.Button(left,text="Derive CH from timestamp photos",command=self.load_concrete_photos).grid(row=r,column=0,columnspan=2,sticky="ew",pady=4); r+=1
        ttk.Button(left,text="SCAN RN ENCASEMENT FOLDER / ZIP",command=self.scan_concrete_folder).grid(row=r,column=0,columnspan=2,sticky="ew",pady=4); r+=1
        ttk.Button(left,text="+ ADD STRUCTURE TO RN EVENT",command=self.add_structure_to_batch).grid(row=r,column=0,columnspan=2,sticky="ew",pady=(8,4)); r+=1
        ttk.Button(left,text="SAVE ENTIRE RN EVENT",command=self.save_concrete_event).grid(row=r,column=0,columnspan=2,sticky="ew",pady=4)

        # Right side is also resizable: drag horizontal dividers between
        # Current Event, Picture 1 sketch, and Concrete Log.
        self.conc_right_pane=tk.PanedWindow(
            right,orient="vertical",sashwidth=8,sashrelief="raised",
            bg="#334155",bd=0
        )
        self.conc_right_pane.pack(fill="both",expand=True)

        event_panel=ttk.Frame(self.conc_right_pane,style="Card.TFrame",padding=6)
        pic=ttk.Frame(self.conc_right_pane,style="Card.TFrame",padding=6)
        log_panel=ttk.Frame(self.conc_right_pane,style="Card.TFrame",padding=6)
        self.conc_right_pane.add(event_panel,minsize=120,height=175,stretch="never")
        self.conc_right_pane.add(pic,minsize=300,height=465,stretch="always")
        self.conc_right_pane.add(log_panel,minsize=120,height=190,stretch="always")

        ttk.Label(event_panel,text="STRUCTURES IN CURRENT RN EVENT",style="Card.TLabel",font=("Segoe UI",13,"bold")).pack(anchor="w")
        self.batch_tree=ttk.Treeview(event_panel,columns=("element","qty","ch","vol","note"),show="headings",height=4)
        for c,t,w in (("element","Element",160),("qty","Qty",55),("ch","Chainage",170),("vol","m³",75),("note","Notes",260)):
            self.batch_tree.heading(c,text=t); self.batch_tree.column(c,width=w,anchor="center" if c!="note" else "w",stretch=True)
        self.batch_tree.pack(fill="both",expand=True,pady=(6,4))
        ttk.Button(event_panel,text="REMOVE SELECTED FROM EVENT",command=self.remove_batch_structure).pack(anchor="e")

        header=ttk.Frame(pic,style="Card.TFrame"); header.pack(fill="x")
        ttk.Label(header,text="CONCRETE ENCASEMENT SKETCH — CHECKLIST PICTURE 1",style="Card.TLabel",font=("Segoe UI",13,"bold")).pack(side="left")
        self.pic1_rn=tk.StringVar()
        ttk.Label(header,text="RN",style="Card.TLabel").pack(side="left",padx=(16,5))
        self.pic1_combo=ttk.Combobox(header,textvariable=self.pic1_rn,state="readonly",width=12)
        self.pic1_combo.pack(side="left")
        self.pic1_combo.bind("<<ComboboxSelected>>",lambda e:self.picture1_select_rn())

        pic_actions=ttk.Frame(pic,style="Card.TFrame"); pic_actions.pack(fill="x",pady=(5,2))
        ttk.Button(pic_actions,text="SELECT ALL",command=self.picture1_select_all).pack(side="left")
        ttk.Button(pic_actions,text="CLEAR ALL",command=self.picture1_clear).pack(side="left",padx=5)
        ttk.Button(pic_actions,text="SAVE PICTURE 1",command=self.save_concrete_picture1).pack(side="right")
        ttk.Button(pic_actions,text="RESET ZOOM",command=lambda:self.concrete_pic_plot.reset_view()).pack(side="right",padx=5)

        self.pic1_status=ttk.Label(
            pic,text="Select an RN to highlight its concrete encasement chainage(s).",
            style="Card.TLabel"
        )
        self.pic1_status.pack(anchor="w",pady=(2,2))

        # Sketch grows/shrinks with the middle pane.
        self.concrete_pic_plot=ChainagePlot(
            pic,self.store,self.alignment,height=260,on_change=self.refresh_all
        )
        self.concrete_pic_plot.pack(fill="both",expand=True,pady=(3,5))

        self.pic1_tree=ttk.Treeview(
            pic,columns=("use","rn","ch","element","date"),show="headings",height=3
        )
        for c,t,w in (("use","Selected",65),("rn","RN",75),("ch","Encasement chainage",175),("element","Element",140),("date","Cast date",95)):
            self.pic1_tree.heading(c,text=t)
            self.pic1_tree.column(c,width=w,anchor="center",stretch=True)
        self.pic1_tree.pack(fill="x")
        self.pic1_tree.bind("<Double-1>",lambda e:self.picture1_toggle_row())
        ttk.Label(
            pic,text=f"Auto-save: {PICTURE1_DIR}\\RN###.png",style="Card.TLabel"
        ).pack(anchor="w",pady=(4,0))

        ttk.Label(log_panel,text="CONCRETE LOG",style="Card.TLabel",font=("Segoe UI",13,"bold")).pack(anchor="w")
        log_wrap=ttk.Frame(log_panel,style="Card.TFrame"); log_wrap.pack(fill="both",expand=True,pady=(5,0))
        self.conc_tree=ttk.Treeview(
            log_wrap,columns=("id","rn","date","team","element","ch","vol","due"),show="headings",height=5
        )
        for c,t,w in (("id","Pour",55),("rn","RN",75),("date","Cast date",95),("team","Team",100),("element","Element",140),("ch","Chainage",130),("vol","m³",70),("due","7 / 14 / 28-day dates",235)):
            self.conc_tree.heading(c,text=t); self.conc_tree.column(c,width=w,anchor="center",stretch=True)
        conc_vscroll=ttk.Scrollbar(log_wrap,orient="vertical",command=self.conc_tree.yview)
        conc_hscroll=ttk.Scrollbar(log_wrap,orient="horizontal",command=self.conc_tree.xview)
        self.conc_tree.configure(yscrollcommand=conc_vscroll.set,xscrollcommand=conc_hscroll.set)
        self.conc_tree.grid(row=0,column=0,sticky="nsew")
        conc_vscroll.grid(row=0,column=1,sticky="ns")
        conc_hscroll.grid(row=1,column=0,sticky="ew")
        log_wrap.rowconfigure(0,weight=1); log_wrap.columnconfigure(0,weight=1)

        self.pic1_selected_ids=set()
        self.refresh_picture1_controls()

        # Restore useful starting proportions after Tk has calculated the window.
        self.after(250,self._set_concrete_default_sashes)

    def _set_concrete_default_sashes(self):
        """Set sensible initial splitter positions; users can drag them afterwards."""
        try:
            total_w=max(self.conc_main_pane.winfo_width(),1000)
            self.conc_main_pane.sash_place(0,max(300,min(380,int(total_w*0.22))),0)

            total_h=max(self.conc_right_pane.winfo_height(),650)
            first=max(135,min(190,int(total_h*0.20)))
            second=max(first+320,min(total_h-145,int(total_h*0.76)))
            self.conc_right_pane.sash_place(0,0,first)
            self.conc_right_pane.sash_place(1,0,second)
        except Exception:
            pass

    def _picture1_rows(self):
        return [r for r in self.store.rows("concrete_pours","rn") if str(r["rn"] or "").strip() and r["start_ch"] is not None and r["end_ch"] is not None and "encasement" in str(r["element"] or "").lower()]

    def refresh_picture1_controls(self):
        if not hasattr(self,"pic1_combo"): return
        rows=self._picture1_rows()
        rns=sorted({normalize_rn(r["rn"]) or str(r["rn"]).strip().upper() for r in rows},key=lambda x:(int(''.join(c for c in x if c.isdigit()) or 0),x))
        self.pic1_combo["values"]=rns
        if self.pic1_rn.get() not in rns: self.pic1_rn.set("")
        self.pic1_selected_ids=getattr(self,"pic1_selected_ids",set()) & {int(r["id"]) for r in rows}
        self._render_picture1_selection()

    def picture1_select_rn(self):
        rn=normalize_rn(self.pic1_rn.get()) or self.pic1_rn.get().strip().upper()
        self.pic1_selected_ids={int(r["id"]) for r in self._picture1_rows() if (normalize_rn(r["rn"]) or str(r["rn"]).strip().upper())==rn}
        self._render_picture1_selection()

    def picture1_select_all(self):
        self.pic1_selected_ids={int(r["id"]) for r in self._picture1_rows()}; self.pic1_rn.set(""); self._render_picture1_selection()

    def picture1_clear(self):
        self.pic1_selected_ids=set(); self.pic1_rn.set(""); self._render_picture1_selection()

    def picture1_toggle_row(self):
        sel=self.pic1_tree.selection()
        if not sel: return
        pid=int(sel[0])
        if pid in self.pic1_selected_ids: self.pic1_selected_ids.remove(pid)
        else: self.pic1_selected_ids.add(pid)
        self._render_picture1_selection()

    def _render_picture1_selection(self):
        if not hasattr(self,"pic1_tree"): return
        rows=self._picture1_rows(); self.pic1_tree.delete(*self.pic1_tree.get_children()); selected_rns=set(); selected_rows=[]
        for r in rows:
            pid=int(r["id"]); checked=pid in self.pic1_selected_ids; rn=normalize_rn(r["rn"]) or str(r["rn"]).strip().upper()
            if checked: selected_rns.add(rn); selected_rows.append(r)
            self.pic1_tree.insert("","end",iid=str(pid),values=("✓" if checked else "□",rn,f"CH{float(r['start_ch']):.1f}–CH{float(r['end_ch']):.1f}",r["element"],r["cast_date"]))
        self.concrete_pic_plot.set_selected_concrete_rns(selected_rns)
        if selected_rows:
            ranges=", ".join(f"{normalize_rn(r['rn']) or r['rn']} CH{float(r['start_ch']):.1f}–CH{float(r['end_ch']):.1f}" for r in selected_rows)
            self.pic1_status.config(text=f"Highlighted {len(selected_rows)} encasement(s): {ranges}")
        else: self.pic1_status.config(text="Select an RN to highlight its concrete encasement chainage(s).")

    def save_concrete_picture1(self):
        selected=[r for r in self._picture1_rows() if int(r["id"]) in self.pic1_selected_ids]
        if not selected: messagebox.showerror("Picture 1","Select at least one concrete encasement."); return
        grouped={}
        for r in selected:
            rn=normalize_rn(r["rn"]) or str(r["rn"]).strip().upper(); grouped.setdefault(rn,[]).append(r)
        try:
            PICTURE1_DIR.mkdir(parents=True,exist_ok=True); saved=[]
            for rn,rn_rows in grouped.items():
                target=PICTURE1_DIR/f"{rn}.png"
                if target.exists() and not messagebox.askyesno("Replace Picture 1?",f"{rn}.png already exists.\\n\\nReplace it?"): continue
                export_rn_picture(target,self.alignment,self.store.rows("kmz_pipes","start_ch"),rn_rows,[rn],source_kind="concrete"); saved.append(str(target))
            if saved: messagebox.showinfo("Picture 1 saved","Saved:\\n\\n"+"\\n".join(saved))
        except Exception as e: messagebox.showerror("Cannot save Picture 1",str(e))

    def _structure_from_form(self):
        element=self.c_element.get(); start=fnum(self.c_start.get(),"Start chainage") if self.c_start.get().strip() else None; end=fnum(self.c_end.get(),"End chainage") if self.c_end.get().strip() else None
        if element=="Pipe encasement" and (start is None or end is None): raise ValueError("Pipe encasement requires start and end chainages.")
        try: qty=int(self.c_qty.get().strip() or "1")
        except ValueError: raise ValueError("Quantity must be a whole number.")
        if qty<1: raise ValueError("Quantity must be at least 1.")
        vol=fnum(self.c_vol.get(),"Volume") if self.c_vol.get().strip() else (qty*SLEEPER_VOLUME if element=="Sleeper" else 0)
        if vol<=0: raise ValueError("Enter a positive cast volume.")
        return {"element":element,"start_ch":min(start,end) if start is not None and end is not None else None,"end_ch":max(start,end) if start is not None and end is not None else None,"volume_m3":vol,"quantity":qty,"evidence_path":" | ".join(x for x in (getattr(self,'enc_start_photo',''),getattr(self,'enc_end_photo','')) if x),"notes":self.c_note.get().strip()}

    def add_structure_to_batch(self):
        try:
            item=self._structure_from_form(); self.concrete_batch.append(item); self.refresh_batch_tree()
            self.c_start.set(""); self.c_end.set(""); self.c_vol.set(""); self.c_qty.set("1"); self.c_note.set(""); self.enc_start_photo=""; self.enc_end_photo=""
        except Exception as e: messagebox.showerror("Cannot add structure",str(e))

    def refresh_batch_tree(self):
        self.batch_tree.delete(*self.batch_tree.get_children())
        for i,x in enumerate(self.concrete_batch):
            ch="—" if x["start_ch"] is None else f"CH{x['start_ch']:g}–CH{x['end_ch']:g}"
            self.batch_tree.insert("","end",iid=str(i),values=(x["element"],x["quantity"],ch,f"{x['volume_m3']:.3f}",x["notes"]))

    def remove_batch_structure(self):
        sel=self.batch_tree.selection()
        if not sel: return
        for i in sorted((int(x) for x in sel),reverse=True): self.concrete_batch.pop(i)
        self.refresh_batch_tree()

    def save_concrete_event(self):
        try:
            if not self.concrete_batch: raise ValueError("Add at least one structure to the RN event first.")
            rn=normalize_rn(self.c_rn.get())
            if not rn: raise ValueError("Enter the shared RN for this concrete event, e.g. RN268.")
            target=fnum(self.c_target.get(),"Target strength")
            shared={"team":self.c_team.get().strip(),"cast_date":self.c_date.get(),"rn":rn,"target_mpa":target}
            ids=self.store.add_pour_batch(shared,self.concrete_batch); d=due_dates(self.c_date.get()); count=len(ids); total=sum(x["volume_m3"] for x in self.concrete_batch)
            self.concrete_batch=[]; self.refresh_batch_tree(); self.refresh_all()
            messagebox.showinfo("RN concrete event saved",f"{rn}: {count} structure record(s) saved\nTotal concrete: {total:.3f} m³\n7-day: {d[7]}\n14-day: {d[14]}\n28-day: {d[28]}")
        except Exception as e: messagebox.showerror("Cannot save RN event",str(e))

    def element_changed(self,*_): self.c_target.set(str(CONCRETE_ELEMENTS[self.c_element.get()]["target_mpa"]))

    def load_concrete_photos(self):
        try:
            d=self._photo_pair(); self.enc_start_photo,self.enc_end_photo=d[0]["path"],d[1]["path"]; self.c_start.set(d[0]["chainage"]); self.c_end.set(d[1]["chainage"])
            if self.c_element.get()=="Pipe encasement": self.c_vol.set(round((d[1]["chainage"]-d[0]["chainage"])*CONCRETE_ELEMENTS["Pipe encasement"]["planned_m3"]/244,3))
        except Exception as e: messagebox.showerror("Photo error",str(e))

    def scan_concrete_folder(self):
        try:
            source=filedialog.askdirectory(title="Select Chainage Pics folder")
            if not source:
                source=filedialog.askopenfilename(title="Or select Chainage Pics ZIP",filetypes=[("ZIP archive","*.zip")])
            if not source: return
            rows=scan_encasement_source(source)
            win=tk.Toplevel(self); win.title("Detected RN encasements"); win.geometry("820x430"); win.configure(bg="#0f172a"); win.transient(self); win.grab_set()
            ttk.Label(win,text="DETECTED ENCASEMENTS — REVIEW BEFORE SAVING",style="Title.TLabel").pack(anchor="w",padx=18,pady=(16,5))
            ttk.Label(win,text="Select an RN and load it into the concrete form. The evidence date is the photo timestamp, not automatically the cast date.").pack(anchor="w",padx=18,pady=(0,10))
            tree=ttk.Treeview(win,columns=("rn","range","length","photos","date","note"),show="headings")
            for c,t,w in (("rn","RN",85),("range","Detected chainage",180),("length","Length m",90),("photos","Photos",75),("date","Evidence date",110),("note","Folder note",190)):
                tree.heading(c,text=t); tree.column(c,width=w,anchor="center")
            for i,row in enumerate(rows):
                note="CH folder treated as RN" if row["folder_corrected"] else ""
                tree.insert("","end",iid=str(i),values=(row["rn"],f"CH{row['start_ch']:g}–CH{row['end_ch']:g}",f"{row['end_ch']-row['start_ch']:.2f}",row["photo_count"],row["evidence_date"],note))
            tree.pack(fill="both",expand=True,padx=18,pady=8); tree.selection_set("0"); tree.focus("0")
            def load_selected():
                chosen=tree.selection()
                if not chosen: return
                row=rows[int(chosen[0])]; self.c_element.set("Pipe encasement"); self.element_changed(); self.c_rn.set(row["rn"]); self.c_start.set(f"{row['start_ch']:g}"); self.c_end.set(f"{row['end_ch']:g}")
                length=row["end_ch"]-row["start_ch"]; self.c_vol.set(f"{length*CONCRETE_ELEMENTS['Pipe encasement']['planned_m3']/ALIGNMENT_LENGTH:.3f}")
                self.enc_start_photo=f"{source}::{row['photos'][0]['name']}"; self.enc_end_photo=f"{source}::{row['photos'][-1]['name']}"
                self.c_note.set(f"Detected from {row['photo_count']} timestamp photos; evidence {row['evidence_date']}")
                self.add_structure_to_batch(); win.destroy(); self.tabs.select(self.conc)
            bar=ttk.Frame(win); bar.pack(fill="x",padx=18,pady=(0,16)); ttk.Button(bar,text="LOAD SELECTED INTO FORM",command=load_selected).pack(side="right"); ttk.Button(bar,text="CANCEL",command=win.destroy).pack(side="right",padx=8)
        except Exception as e: messagebox.showerror("Folder scan error",str(e))

    def save_pour(self):
        # Backward-compatible command path. New workflow saves RN events in batches.
        self.add_structure_to_batch(); self.save_concrete_event()

    def _results(self):
        top=ttk.Frame(self.results,style="Card.TFrame",padding=16); top.pack(fill="x",padx=18,pady=18)
        ttk.Label(top,text="RN-FIRST CONCRETE LAB RESULT  |  FILENAME RN = MASTER",style="Card.TLabel",font=("Segoe UI",13,"bold")).grid(row=0,column=0,columnspan=7,sticky="w",pady=(0,8))
        self.r_rn=tk.StringVar(); self.r_pour=tk.StringVar(); self.r_age=tk.StringVar(value="7"); self.r_value=tk.StringVar(); self.r_date=tk.StringVar(value=date.today().isoformat()); self.r_ref=tk.StringVar(); self.r_cast=tk.StringVar()
        ttk.Button(top,text="UPLOAD LAB RESULT PDF",command=self.upload_lab_pdf).grid(row=1,column=0,padx=(0,12),sticky="ew")
        for i,(lab,var) in enumerate((("RN",self.r_rn),("Cast date",self.r_cast),("Age days",self.r_age),("Result MPa",self.r_value),("Result date",self.r_date),("Lab ref",self.r_ref)),start=1):
            ttk.Label(top,text=lab,style="Card.TLabel").grid(row=0,column=i,sticky="w",padx=5); ttk.Entry(top,textvariable=var,width=14).grid(row=1,column=i,padx=5)
        ttk.Button(top,text="SAVE RN RESULT",command=self.save_result).grid(row=1,column=7,padx=12)
        self.pdf_preview=ttk.Label(top,text="Filename RN has priority. One result is applied to every matching structure under that RN/date.",style="Card.TLabel")
        self.pdf_preview.grid(row=2,column=0,columnspan=8,sticky="w",pady=(10,0))
        middle=ttk.Frame(self.results); middle.pack(fill="both",expand=True,padx=18,pady=(0,10)); middle.columnconfigure(0,weight=3); middle.columnconfigure(1,weight=2); middle.rowconfigure(0,weight=1)
        graph_box=ttk.Frame(middle,style="Card.TFrame",padding=12); graph_box.grid(row=0,column=0,sticky="nsew",padx=(0,8)); ttk.Label(graph_box,text="STRENGTH DEVELOPMENT vs TARGET",style="Card.TLabel",font=("Segoe UI",13,"bold")).pack(anchor="w")
        self.strength_plot=StrengthPlot(graph_box,self.store,self.r_pour); self.strength_plot.pack(fill="both",expand=True,pady=(6,0))
        log_box=ttk.Frame(middle,style="Card.TFrame",padding=12); log_box.grid(row=0,column=1,sticky="nsew"); ttk.Label(log_box,text="RESULTS LOG",style="Card.TLabel",font=("Segoe UI",13,"bold")).pack(anchor="w")
        self.result_tree=ttk.Treeview(log_box,columns=("pour","age","result","required","variance","status"),show="headings",height=8)
        for c,t,w in (("pour","Pour",48),("age","Age",55),("result","MPa",65),("required","Required",72),("variance","Variance",72),("status","Status",70)): self.result_tree.heading(c,text=t); self.result_tree.column(c,width=w,anchor="center")
        self.result_tree.pack(fill="both",expand=True,pady=6); self.result_tree.bind("<<TreeviewSelect>>",self.select_result)
        box=ttk.Frame(self.results,style="Card.TFrame",padding=12); box.pack(fill="both",expand=True,padx=18,pady=(0,18)); ttk.Label(box,text="UPCOMING / OVERDUE CUBE RESULTS",style="Card.TLabel",font=("Segoe UI",13,"bold")).pack(anchor="w")
        self.due_tree=ttk.Treeview(box,columns=("pour","element","cast","age","due","status"),show="headings",height=6)
        for c,t in (("pour","Pour"),("element","Element"),("cast","Cast date"),("age","Age"),("due","Due date"),("status","Action")): self.due_tree.heading(c,text=t); self.due_tree.column(c,anchor="center",width=150)
        self.due_tree.pack(fill="both",expand=True,pady=8)
        self.pending_lab_pdf=None

    def upload_lab_pdf(self):
        try:
            path=filedialog.askopenfilename(title="Select concrete lab result",filetypes=[("PDF lab result","*.pdf")])
            if not path: return
            info=parse_concrete_lab_pdf(path); self.pending_lab_pdf=info
            self.r_rn.set(info["rn"]); self.r_cast.set(info["cast_date"] or ""); self.r_age.set(str(info["age_days"] or "")); self.r_value.set(f"{info['average_mpa']:.1f}"); self.r_date.set(info["result_date"] or date.today().isoformat()); self.r_ref.set(Path(path).name)
            matches,match_mode=self.store.resolve_lab_structures(info["rn"],info["cast_date"])
            body_note=""
            if info["body_rns"] and info["rn"] not in info["body_rns"]:
                body_note=f" | PDF body says {', '.join(info['body_rns'])}; filename RN retained"
            structures=", ".join(f"{p['element']} x{p['quantity']}" for p in matches) or "NO MATCHED STRUCTURES"
            if match_mode=="cast_date" and matches:
                old_rns=sorted({normalize_rn(p["rn"]) or str(p["rn"] or "blank") for p in matches})
                match_note=f" | FILENAME RN MASTER: {', '.join(old_rns)} -> {info['rn']}"
            elif match_mode=="rn":
                match_note=" | matched by RN"
            else:
                match_note=""
            req_note=f" | target {info['required_mpa']:.1f} MPa" if info.get("required_mpa") is not None else ""
            self.pdf_preview.config(text=f"{info['rn']} | {info['age_days'] or '?'}-day | {info['average_mpa']:.1f} MPa | {len(matches)} record(s): {structures}{req_note}{match_note}{body_note}")
        except Exception as e: messagebox.showerror("Lab PDF import",str(e))

    def save_result(self):
        try:
            rn=normalize_rn(self.r_rn.get()); age=int(self.r_age.get()); value=fnum(self.r_value.get(),"Result"); cast=self.r_cast.get().strip() or None
            if not rn: raise ValueError("Enter an RN or upload an RN-named PDF.")
            pdf=self.pending_lab_pdf or {}; body=", ".join(pdf.get("body_rns",[])); path=pdf.get("path","")
            required=pdf.get("required_mpa")
            pours=self.store.add_strength_for_rn(rn,cast,age,value,self.r_date.get(),self.r_ref.get(),path,body,required)
            self.r_pour.set(str(pours[0]["id"])); target=float(pours[0]["target_mpa"]); status=cube_status(target,seven=value if age==7 else None,twenty_eight=value if age==28 else None)
            if age in (7,28): ok,threshold,diff=status["seven" if age==7 else "twenty_eight"]; msg=f"{'PASS' if ok else 'FAIL'} — {value:.1f} MPa vs {threshold:.1f} MPa. Applied to {len(pours)} structure record(s) under {rn}."
            else: msg=f"{age}-day monitoring result saved to {len(pours)} structure record(s) under {rn}."
            self.pending_lab_pdf=None; self.refresh_all(); messagebox.showinfo("RN strength assessment",msg)
        except Exception as e: messagebox.showerror("Cannot save RN result",str(e))

    def select_result(self,*_):
        selected=self.result_tree.selection()
        if selected:
            self.r_pour.set(str(self.result_tree.item(selected[0],"values")[0])); self.strength_plot.draw()

    def _merger(self):
        top=ttk.Frame(self.merger,style="Card.TFrame",padding=14); top.pack(fill="x",padx=18,pady=18)
        self.m_lab=tk.StringVar(); self.m_raw=tk.StringVar(); self.m_register=tk.StringVar(); self.m_output=tk.StringVar(); self.m_recursive=tk.BooleanVar(value=True); self.m_overwrite=tk.BooleanVar(value=False); self.lab_matches=[]; self.lab_unmatched=[]; self.lab_reg_missing=[]
        def folder_row(row,label,var,file=False):
            ttk.Label(top,text=label,style="Card.TLabel").grid(row=row,column=0,sticky="w",pady=4); ttk.Entry(top,textvariable=var,width=90).grid(row=row,column=1,sticky="ew",padx=8)
            cmd=(lambda: var.set(filedialog.askopenfilename(filetypes=[("Excel register","*.xlsx")]) or var.get())) if file else (lambda: var.set(filedialog.askdirectory() or var.get()))
            ttk.Button(top,text="Browse",command=cmd).grid(row=row,column=2)
        top.columnconfigure(1,weight=1); folder_row(0,"Lab results folder",self.m_lab); folder_row(1,"Raw data folder",self.m_raw); folder_row(2,"Inspection register",self.m_register,True); folder_row(3,"Merged results folder",self.m_output)
        opts=ttk.Frame(top,style="Card.TFrame"); opts.grid(row=4,column=1,sticky="w",pady=5); ttk.Checkbutton(opts,text="Scan subfolders",variable=self.m_recursive).pack(side="left",padx=(0,18)); ttk.Checkbutton(opts,text="Overwrite existing PDFs",variable=self.m_overwrite).pack(side="left")
        actions=ttk.Frame(top,style="Card.TFrame"); actions.grid(row=5,column=1,sticky="w",pady=8); ttk.Button(actions,text="PREVIEW MATCHES",command=self.preview_merger).pack(side="left",padx=(0,8)); ttk.Button(actions,text="MERGE READY FILES",command=self.run_merger).pack(side="left")
        box=ttk.Frame(self.merger,style="Card.TFrame",padding=12); box.pack(fill="both",expand=True,padx=18,pady=(0,18)); ttk.Label(box,text="CONCRETE LAB PACKS — Coverpage → Lab report → Raw data",style="Card.TLabel",font=("Segoe UI",13,"bold")).pack(anchor="w")
        self.merge_tree=ttk.Treeview(box,columns=("job","rn","status","description","lab","raw","output"),show="headings")
        for c,t,w in (("job","Reference",80),("rn","RN",70),("status","Status",120),("description","Inspection description",250),("lab","Lab PDFs",80),("raw","Raw PDFs",80),("output","Output",260)): self.merge_tree.heading(c,text=t); self.merge_tree.column(c,width=w,anchor="center" if c not in ("description","output") else "w")
        self.merge_tree.pack(fill="both",expand=True,pady=8)

    def preview_merger(self):
        try:
            if not self.m_lab.get() or not self.m_raw.get(): raise ValueError("Select both Lab Results and Raw Data folders.")
            self.lab_matches,self.lab_unmatched,self.lab_reg_missing=preview_lab(self.m_lab.get(),self.m_raw.get(),self.m_register.get(),self.m_recursive.get()); self.refresh_merger_tree()
            ready=sum(m.ready for m in self.lab_matches); messagebox.showinfo("Preview complete",f"{len(self.lab_matches)} lab jobs found\n{ready} ready to merge\n{len(self.lab_reg_missing)} register inspections missing lab files")
        except Exception as e: messagebox.showerror("Preview error",str(e))

    def refresh_merger_tree(self):
        self.merge_tree.delete(*self.merge_tree.get_children())
        for m in self.lab_matches: self.merge_tree.insert("","end",values=(m.job,f"RN{m.rn}" if m.rn else "",m.status,m.description,len(m.lab_order),len(m.raw),m.output.name if m.output else ""))

    def run_merger(self):
        try:
            if not self.lab_matches: self.preview_merger()
            if not self.m_output.get(): raise ValueError("Select the Merged Results output folder.")
            done=merge_ready(self.lab_matches,self.m_output.get(),self.m_overwrite.get()); report=Path(self.m_output.get())/"concrete_lab_merger_report.xlsx"; write_report(report,self.lab_matches,self.lab_unmatched,self.lab_reg_missing); self.refresh_merger_tree()
            messagebox.showinfo("Merge complete",f"{len(done)} PDF pack(s) merged.\nReport: {report}")
        except Exception as e: messagebox.showerror("Merge error",str(e))

    def _league(self):
        top=ttk.Frame(self.league,style="Card.TFrame",padding=14); top.pack(fill="x",padx=18,pady=18); self.l_team=tk.StringVar(); self.l_disc=tk.StringVar(value="Pipeline"); self.l_start=tk.StringVar(value=date.today().isoformat()); self.l_end=tk.StringVar(value=date.today().isoformat()); self.l_target=tk.StringVar()
        for i,(lab,var) in enumerate((("Team",self.l_team),("Discipline",self.l_disc),("Period start",self.l_start),("Period end",self.l_end),("Target",self.l_target))): ttk.Label(top,text=lab,style="Card.TLabel").grid(row=0,column=i,sticky="w",padx=5); (ttk.Combobox(top,textvariable=var,values=("Pipeline","Concrete"),state="readonly",width=14) if lab=="Discipline" else ttk.Entry(top,textvariable=var,width=16)).grid(row=1,column=i,padx=5)
        ttk.Button(top,text="SET TARGET",command=self.set_target).grid(row=1,column=5,padx=8); ttk.Button(top,text="REFRESH",command=self.refresh_league).grid(row=1,column=6,padx=8)
        box=ttk.Frame(self.league,style="Card.TFrame",padding=12); box.pack(fill="both",expand=True,padx=18,pady=(0,18)); self.league_tree=ttk.Treeview(box,columns=("rank","team","disc","target","actual","score"),show="headings")
        for c,t in (("rank","Rank"),("team","Team"),("disc","Discipline"),("target","Target"),("actual","Actual"),("score","Score %")): self.league_tree.heading(c,text=t); self.league_tree.column(c,anchor="center")
        self.league_tree.pack(fill="both",expand=True)
        ttk.Button(box,text="EXPORT ALL LOGS TO CSV",command=self.export).pack(anchor="e",pady=(10,0))

    def set_target(self):
        try: self.store.set_target(self.l_team.get().strip(),self.l_disc.get(),self.l_start.get(),self.l_end.get(),fnum(self.l_target.get(),"Target")); self.refresh_league()
        except Exception as e: messagebox.showerror("Target error",str(e))

    def refresh_league(self):
        if not hasattr(self,"league_tree"): return
        self.league_tree.delete(*self.league_tree.get_children())
        for i,r in enumerate(self.store.standings(self.l_start.get(),self.l_end.get()),1): self.league_tree.insert("", "end", values=(i,r["team"],r["discipline"],f"{r['target']:.1f} {r['unit']}",f"{r['actual']:.1f} {r['unit']}",f"{r['score']:.1f}%"))

    def export(self):
        folder=filedialog.askdirectory();
        if folder: self.store.export_csvs(folder); messagebox.showinfo("Export complete",f"CSV logs saved to:\n{folder}")

    def refresh_target_plan(self):
        try:
            target=datetime.strptime(self.plan_target.get().strip(),"%Y-%m-%d").date(); today=date.today(); scope=self.plan_scope.get()
            if scope=="Pipe laying":
                actual=self.store.pipe_summary()[0]; total=ALIGNMENT_LENGTH; unit="m"; row=self.store.db.execute("SELECT MIN(installed_date) FROM pipe_installations WHERE laid=1").fetchone(); start_text=row[0] or date.today().isoformat()
            elif scope=="Concrete works":
                actual=sum(self.store.concrete_summary().values()); total=sum(x["planned_m3"] for x in CONCRETE_ELEMENTS.values()); unit="m³"; row=self.store.db.execute("SELECT MIN(cast_date) FROM concrete_pours").fetchone(); start_text=row[0]
            else:
                actual=sum(r["quantity"] for r in self.store.rows("concrete_pours") if r["element"]=="Sleeper"); total=float(SLEEPERS_PLANNED); unit="sleepers"; row=self.store.db.execute("SELECT MIN(cast_date) FROM concrete_pours WHERE element='Sleeper'").fetchone(); start_text=row[0]
            start=datetime.strptime(start_text,"%Y-%m-%d").date() if start_text else today
            if target<start: raise ValueError("Target date cannot be earlier than the first recorded production date.")
            total_days=max(1,working_days(start,target)); elapsed=0 if today<start else working_days(start,min(today,target)); planned_pct=min(100.0,elapsed/total_days*100); actual_pct=min(100.0,actual/total*100 if total else 0); variance=actual_pct-planned_pct; attainment=(actual_pct/planned_pct*100) if planned_pct>0 else 0
            days_left=working_days(today+timedelta(days=1),target); remaining=max(0.0,total-actual); required=remaining/days_left if days_left else remaining
            self.plan_values[0].config(text=f"{planned_pct:.1f}%"); self.plan_values[1].config(text=f"{actual_pct:.1f}%"); self.plan_values[2].config(text=f"{variance:+.1f} pp",foreground="#22c55e" if variance>=0 else "#ef4444"); self.plan_values[3].config(text=f"{attainment:.1f}%"); self.plan_values[4].config(text=f"{required:.2f} {unit}")
            status="AHEAD OF PLAN" if variance>0.05 else ("ON PLAN" if variance>=-0.05 else "BEHIND PLAN")
            self.plan_context.config(text=f"{status}  |  Plan start: {start}  |  Target: {target}  |  {days_left} working day(s) remaining  |  {remaining:.2f} {unit} still required",foreground="#22c55e" if variance>=0 else "#ef4444")
            self.store.set_setting("plan_target",target.isoformat()); self.store.set_setting("plan_scope",scope)
        except Exception as e:
            messagebox.showerror("Plan calculation",str(e))

    def refresh_all(self):
        total,counts=self.store.pipe_summary(); concrete=self.store.concrete_summary(); total_plan=sum(x["planned_m3"] for x in CONCRETE_ELEMENTS.values()); cast=sum(concrete.values()); sleepers=sum(r["quantity"] for r in self.store.rows("concrete_pours") if r["element"]=="Sleeper"); due=self.store.open_pour_schedule(); overdue=sum(1 for x in due if x[3]<=0)
        self.pipe_metric.config(text=f"{total:.1f} / 244.0 m"); self.pipe_bar.draw(total/244*100,f"{total/244*100:.1f}%")
        self.conc_metric.config(text=f"{cast:.2f} / {total_plan:.2f} m³"); self.conc_bar.draw(cast/total_plan*100,f"{cast/total_plan*100:.1f}%")
        self.sleep_metric.config(text=f"{sleepers} / {SLEEPERS_PLANNED}"); self.sleep_bar.draw(sleepers/SLEEPERS_PLANNED*100,f"{SLEEPERS_PLANNED-sleepers} remaining")
        self.alert_metric.config(text=str(overdue)); self.alert_bar.draw(100 if overdue else 0,"Action required" if overdue else "Up to date")
        self.inventory.delete(*self.inventory.get_children())
        for name,d in PIPE_TYPES.items(): self.inventory.insert("","end",text=name,values=(d["planned"],counts.get(name,0),max(0,d["planned"]-counts.get(name,0)),f"{d['length']:.3f} m"))
        self.pipe_tree.delete(*self.pipe_tree.get_children())
        for r in self.store.rows("pipe_installations"):
            tag="off" if not r["laid"] else ("missing" if r["survey_missing"] else "")
            self.pipe_tree.insert("","end",iid=str(r["id"]),tags=(tag,) if tag else (),values=(r["installed_date"],r["team"],f"CH{r['start_ch']:.1f}–CH{r['end_ch']:.1f}",f"{r['length_m']:.2f}",r["pipe_type"],r["rn"] or "Pending","ON" if r["laid"] else "OFF","OUTSTANDING" if r["survey_missing"] else "Received"))
        self.conc_tree.delete(*self.conc_tree.get_children())
        for r in self.store.rows("concrete_pours"):
            d=due_dates(r["cast_date"]); ch="—" if r["start_ch"] is None else f"CH{r['start_ch']:.1f}–CH{r['end_ch']:.1f}"; self.conc_tree.insert("","end",values=(r["id"],r["rn"] or "Pending",r["cast_date"],r["team"],r["element"],ch,f"{r['volume_m3']:.3f}",f"{d[7]} / {d[14]} / {d[28]}"))
        self.due_tree.delete(*self.due_tree.get_children())
        for p,age,d,delta in due:
            status="OVERDUE" if delta<0 else ("DUE TODAY" if delta==0 else f"In {delta} days"); self.due_tree.insert("","end",values=(p["id"],p["element"],p["cast_date"],f"{age}-day",d,status))
        self.result_tree.delete(*self.result_tree.get_children())
        for r in self.store.all_strength_results():
            age,val,target=int(r["age_days"]),float(r["result_mpa"]),float(r["target_mpa"])
            req=target*.60 if age==7 else (target if age==28 else None)
            status="MONITOR" if req is None else ("PASS" if val>=req else "FAIL")
            self.result_tree.insert("","end",values=(r["pour_id"],f"{age}d",f"{val:.1f}","—" if req is None else f"{req:.1f}","—" if req is None else f"{val-req:+.1f}",status))
        if not self.r_pour.get() and self.store.rows("concrete_pours"):
            self.r_pour.set(str(self.store.rows("concrete_pours")[0]["id"]))
        self.strength_plot.draw()
        self.plot.draw(); self.pipe_plot.draw(); self.refresh_league()
        self.refresh_target_plan()
        if hasattr(self,"pic1_combo"): self.refresh_picture1_controls()


if __name__ == "__main__":
    App().mainloop()
