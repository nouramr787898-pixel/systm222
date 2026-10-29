/* Silent opening. SVG geometry avoids browser-dependent transform origins. */
(()=>{
 const intro=document.getElementById('ehkamIntro');if(!intro)return;
 const previous=document.activeElement;
 const blocked=[...document.body.children].filter(el=>el!==intro&&!['SCRIPT','STYLE'].includes(el.tagName)&&!el.inert);
 blocked.forEach(el=>el.inert=true);
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 const eyes=[...intro.querySelectorAll('.intro-face-eye')],smile=intro.querySelector('.intro-face-smile');
 let finishTimer,readyTimer,frame,paint,started=false,ready=false,closed=false;
 intro.style.animation='none';
 function finish(){if(closed)return;closed=true;clearTimeout(finishTimer);clearTimeout(readyTimer);cancelAnimationFrame(frame);cancelAnimationFrame(paint);document.removeEventListener('visibilitychange',whenVisible);blocked.forEach(el=>el.inert=false);intro.remove();if(previous&&previous!==document.body&&previous.isConnected)previous.focus({preventScroll:true})}
 function animateFace(start){
  if(closed)return;
  const elapsed=performance.now()-start;
  const t=Math.max(0,Math.min(1,(elapsed-650)/900));
  const scale=t<.3?1-.88*t/.3:t<.7?.12:.12+.88*(t-.7)/.3;
  eyes.forEach(eye=>eye.setAttribute('ry',String(3.6*scale)));
  smile.setAttribute('stroke-dashoffset',String(26*(1-Math.max(0,Math.min(1,(elapsed-1350)/450)))));
  if(elapsed<1800)frame=requestAnimationFrame(()=>animateFace(start));
 }
 function start(){
  if(started||closed||document.hidden||!ready)return;
  started=true;clearTimeout(readyTimer);
  // Let the decoded SVG image paint before starting the shared clock.
  paint=requestAnimationFrame(()=>{paint=requestAnimationFrame(()=>{
   if(closed)return;
   intro.classList.add('intro-running');intro.style.removeProperty('animation');
   if(reduced){eyes.forEach(eye=>eye.setAttribute('ry','3.6'));smile.setAttribute('stroke-dashoffset','0')}
   else if(smile&&eyes.length)animateFace(performance.now());
   finishTimer=setTimeout(finish,reduced?1100:6800);
  })});
 }
 function whenVisible(){if(!document.hidden)start()}
 function markReady(){ready=true;start()}
 intro.addEventListener('keydown',event=>{if(event.key==='Escape')finish()});
 document.addEventListener('visibilitychange',whenVisible);
 intro.focus({preventScroll:true});
 // A missing/slow image must not trap the user behind the opening.
 readyTimer=setTimeout(finish,4000);
 const image=new Image();image.src=new URL('./ehkam-icon.png',document.baseURI).href;
 if(image.decode)image.decode().then(markReady,finish);
 else if(image.complete){if(image.naturalWidth)markReady();else finish()}
 else{image.onload=markReady;image.onerror=finish}
})();
