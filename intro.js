/* Local opening and recorded Arabic voice; no external voice service or user data. */
(()=>{
 const intro=document.getElementById('ehkamIntro');if(!intro)return;
 const previous=document.activeElement;
 const blocked=[...document.body.children].filter(el=>el!==intro&&!['SCRIPT','STYLE'].includes(el.tagName)&&!el.inert);
 blocked.forEach(el=>el.inert=true);
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 const audio=document.getElementById('introAudio'),button=document.getElementById('introSound'),status=document.getElementById('introSoundStatus');
 const duration=reduced?1100:6800,deadline=performance.now()+duration;
 let finishTimer,voiceTimer,closed=false,playing=false,userRequest=false;
 function remove(){if(closed)return;closed=true;clearTimeout(finishTimer);clearTimeout(voiceTimer);document.removeEventListener('visibilitychange',onVisibility);audio.pause();blocked.forEach(el=>el.inert=false);intro.remove();if(previous&&previous!==document.body&&previous.isConnected)previous.focus({preventScroll:true})}
 function finish(){if(playing&&userRequest){intro.classList.add('intro-audio-hold');finishTimer=setTimeout(remove,5000)}else remove()}
 function idle(){playing=false;button.textContent='إعادة تشغيل الصوت';button.setAttribute('aria-label','إعادة تشغيل صوت المقدمة');button.setAttribute('aria-pressed','false');if(performance.now()>=deadline)remove()}
 async function play(manual=false){
  if(closed||document.hidden)return;userRequest=manual;button.disabled=true;
  if(manual&&deadline-performance.now()<3000)intro.classList.add('intro-audio-hold');
  try{audio.currentTime=0;await audio.play();if(closed){audio.pause();return}playing=true;status.textContent='';button.textContent='كتم الصوت';button.setAttribute('aria-label','كتم صوت المقدمة');button.setAttribute('aria-pressed','true')}
  catch(error){if(closed)return;playing=false;intro.classList.remove('intro-audio-hold');if(error.name==='NotAllowedError'){button.textContent='تشغيل صوت المقدمة';button.setAttribute('aria-label','تشغيل صوت المقدمة');status.textContent='اضغط لتشغيل الصوت'}else{status.textContent='تعذر تشغيل الصوت؛ ستستمر المقدمة';button.hidden=true}}
  finally{button.disabled=false}
 }
 audio.addEventListener('ended',idle);audio.addEventListener('error',()=>{idle();status.textContent='تعذر تشغيل الصوت؛ ستستمر المقدمة';button.hidden=true});
 button.addEventListener('click',()=>{if(playing){audio.pause();idle();intro.classList.remove('intro-audio-hold')}else play(true)});
 intro.addEventListener('keydown',event=>{if(event.key==='Escape')remove()});
 function onVisibility(){if(document.hidden&&!closed){audio.pause();idle()}}
 document.addEventListener('visibilitychange',onVisibility);
 intro.focus({preventScroll:true});
 // Reduced-motion users get a short static introduction without automatic audio.
 if(!reduced)voiceTimer=setTimeout(()=>{button.hidden=false;play()},2500);
 finishTimer=setTimeout(finish,duration);
})();
