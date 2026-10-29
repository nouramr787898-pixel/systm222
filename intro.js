/* Opening animation only: no audio, session, credential or network changes. */
(()=>{
 const intro=document.getElementById('ehkamIntro');if(!intro)return;
 const previous=document.activeElement;
 const blocked=[...document.body.children].filter(el=>el!==intro&&!['SCRIPT','STYLE'].includes(el.tagName)&&!el.inert);
 blocked.forEach(el=>el.inert=true);
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 let timer;
 function finish(){clearTimeout(timer);blocked.forEach(el=>el.inert=false);intro.remove();if(previous&&previous!==document.body&&previous.isConnected)previous.focus({preventScroll:true})}
 intro.addEventListener('keydown',event=>{if(event.key==='Escape')finish()});
 intro.focus({preventScroll:true});
 timer=setTimeout(finish,reduced?1100:6800);
})();
