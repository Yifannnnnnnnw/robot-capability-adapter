import json, re, glob
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt, matplotlib.patches as mp
from matplotlib.patches import Rectangle
import numpy as np
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Helvetica','Arial','DejaVu Sans'],'axes.linewidth':0.6,'svg.fonttype':'none','pdf.fonttype':42})

def rows(b,ph):
    try: return [json.loads(l) for l in open(f'{b}/traces/{ph}')]
    except: return []
def otext(o): return ' '.join((x.get('content','') if isinstance(x.get('content',''),str) else json.dumps(x.get('content',''))) for x in (o if isinstance(o,list) else []))
def itype(r):
    he=any(a.get('name') in ('local_exec','execute_python') for a in (r.get('actions') or []))
    t=otext(r.get('observations')); err=bool(re.search(r'"exit_code":\s*[1-9]',t) or 'Traceback (most recent call last)' in t)
    return 2 if (he and err) else (1 if he else 0)
def seq(b):
    s=[itype(r) for r in rows(b,'01_study.jsonl')]; ns=len(s); s+=[itype(r) for r in rows(b,'02_gen_algo.jsonl')]; return s,ns
def tok(b): return sum((r.get('token_usage') or {}).get('out',0) for ph in ('01_study.jsonl','02_gen_algo.jsonl') for r in rows(b,ph))

A='artifacts/'
tracks=[('Opus 4.8',A+'from_scratch_xmodel/so101_opus48_rr1'),
        ('DeepSeek',A+'from_scratch_xmodel/so101_deepseek_rr2'),
        ('Haiku 4.5',A+'from_scratch_xmodel/so101_haiku45_rr2'),
        ('Skydio X2',A+'from_scratch_aerial_v2/skydio_x2'),
        ('ANYmal-C',A+'from_scratch_quad_v2/anymal'),
        ('H1',A+'from_scratch_humanoid_v3/h1')]
C={0:'#dcdddf',1:'#3f9b8c',2:'#d1495b'}
fig=plt.figure(figsize=(7.0,3.5))
gs=fig.add_gridspec(2,1,height_ratios=[3.0,1.45],hspace=0.18)
axA=fig.add_subplot(gs[0]); axB=fig.add_subplot(gs[1])

seqs=[seq(b) for _,b in tracks]; maxlen=max(len(s) for s,_ in seqs); n=len(tracks)
axA.add_patch(Rectangle((-0.5,n-3-0.5),maxlen+1,3,facecolor='#f0f5f4',ec='none',zorder=0))
for i,((lab,_),(s,ns)) in enumerate(zip(tracks,seqs)):
    y=n-1-i
    for x,v in enumerate(s):
        axA.add_patch(Rectangle((x,y-0.36),1,0.72,facecolor=C[v],ec='white',lw=0.35,zorder=2))
    axA.plot([ns,ns],[y-0.46,y+0.46],color='#222',lw=1.0,zorder=3)
    axA.text(-1.0,y,lab,ha='right',va='center',fontsize=8,zorder=3)
    axA.text(len(s)+0.8,y,len(s),ha='left',va='center',fontsize=7,color='#999',zorder=3)
axA.set_xlim(-12,maxlen+4); axA.set_ylim(-0.65,n-1+0.55); axA.axis('off')
axA.plot([-9.2,-9.2],[n-3-0.46,n-1+0.46],color='#3f9b8c',lw=2.0)
axA.plot([-9.2,-9.2],[-0.46,2+0.46],color='#b9b9ad',lw=2.0)
axA.text(-9.9,n-2,'SO-101',rotation=90,ha='center',va='center',fontsize=6.8,color='#2c6f64')
axA.text(-9.9,1,'morph.',rotation=90,ha='center',va='center',fontsize=6.8,color='#8a8a78')

# Panel b: SO-101 trio, horizontal STACKED composition bars (matches a's colors)
trio=tracks[:3]
comp=[]; tks=[]
for _,b in trio:
    s,_=seq(b); comp.append([s.count(0),s.count(1),s.count(2)]); tks.append(round(tok(b)/1000))
comp=np.array(comp); yb=np.arange(len(trio))[::-1]  # Opus on top
left=np.zeros(len(trio))
for k,lab in [(0,'read / plan'),(1,'execute (clean)'),(2,'execute (error)')]:
    axB.barh(yb,comp[:,k],left=left,height=0.62,color=C[k],edgecolor='white',linewidth=0.5,zorder=2)
    left=left+comp[:,k]
for j,(name,_) in enumerate(trio):
    tot=comp[j].sum(); y=yb[j]
    axB.text(-1.0,y,name,ha='right',va='center',fontsize=8)
    axB.text(tot+1.0,y,f'{tot} it · {comp[j][2]} err · {tks[j]}k tok',ha='left',va='center',fontsize=6.9,color='#666')
axB.set_xlim(-12,maxlen+4); axB.set_ylim(-0.6,len(trio)-0.4); axB.axis('off')
axB.text((maxlen)/2-1,-0.62,'iterations (stacked by action type), SO-101 same task',ha='center',va='top',fontsize=7,color='#888')

h=[mp.Patch(color=C[0],label='read / plan'),mp.Patch(color=C[1],label='execute (clean)'),
   mp.Patch(color=C[2],label='execute (error)'),plt.Line2D([0],[0],color='#222',lw=1.0,label='study | generate')]
fig.legend(handles=h,loc='lower center',ncol=4,frameon=False,fontsize=7.2,bbox_to_anchor=(0.5,-0.06))
fig.savefig('paper/latex/figures/synth_trajectory.pdf',bbox_inches='tight')
fig.savefig('paper/figures_wip/synth_trajectory.svg',bbox_inches='tight')
fig.savefig('paper/figures_wip/synth_trajectory.png',dpi=200,bbox_inches='tight')
print('saved v6')
