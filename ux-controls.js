/* Shared accessible controls. Stored dates and passwords are never rewritten here. */
function asciiDigits(value){return String(value).replace(/[٠-٩]/g,c=>'٠١٢٣٤٥٦٧٨٩'.indexOf(c)).replace(/[۰-۹]/g,c=>'۰۱۲۳۴۵۶۷۸۹'.indexOf(c))}
function dateTemplate(raw){const digits=asciiDigits(raw).replace(/\D/g,'').slice(0,8).padEnd(8,'_');return digits.slice(0,2)+'/'+digits.slice(2,4)+'/'+digits.slice(4)}
document.addEventListener('beforeinput',event=>{
 const input=event.target;if(!input.matches('input[data-date]')||!['insertText','deleteContentBackward','deleteContentForward'].includes(event.inputType))return;
 if(event.inputType==='insertText'&&(event.data||'').length>1)return;
 if(event.inputType==='insertText'&&!/^\d+$/.test(asciiDigits(event.data||''))) {event.preventDefault();return}
 event.preventDefault();const positions=[0,1,3,4,6,7,8,9];let chars=dateTemplate(input.value).split(''),start=input.selectionStart,end=input.selectionEnd;
 for(const pos of positions)if(pos>=start&&pos<end)chars[pos]='_';
 if(event.inputType==='insertText'){
  for(const digit of asciiDigits(event.data)){const pos=positions.find(p=>p>=start);if(pos===undefined)break;chars[pos]=digit;start=pos+1;if(start===2||start===5)start++}
 }else{
  const pos=event.inputType==='deleteContentBackward'?positions.filter(p=>p<start).pop():positions.find(p=>p>=start);
  if(end===start&&pos!==undefined){chars[pos]='_';start=pos}
 }
 input.value=chars.join('');input.setSelectionRange(start,start);input.dispatchEvent(new Event('input',{bubbles:true}));
});
document.addEventListener('input',event=>{const input=event.target;if(!input.matches('input[data-date]'))return;const caret=input.selectionStart;const raw=asciiDigits(input.value);if(raw&&!/^[0-9_]{2}\/[0-9_]{2}\/[0-9_]{4}$/.test(raw)&&!/^\d{1,2}\/\d{1,2}\/\d{4}$/.test(raw)){input.value=dateTemplate(raw);input.setSelectionRange(Math.min(caret,10),Math.min(caret,10))}else input.value=raw});
document.addEventListener('keydown',event=>{const input=event.target;if(!input.matches('input[data-date]'))return;if(event.key==='/'){event.preventDefault();const next=input.selectionStart<3?3:6;input.setSelectionRange(next,next===3?5:10)}});
function enhancePasswords(){for(const input of document.querySelectorAll('input[type=password]')){
 if(input.dataset.eyeReady)continue;input.dataset.eyeReady='1';const wrapper=document.createElement('span');wrapper.className='password-control';input.before(wrapper);wrapper.append(input);
 const button=document.createElement('button');button.type='button';button.className='password-eye';button.textContent='◉';button.setAttribute('aria-label','إظهار كلمة المرور');button.setAttribute('aria-pressed','false');
 button.onclick=()=>{const show=input.type==='password';input.type=show?'text':'password';button.setAttribute('aria-label',show?'إخفاء كلمة المرور':'إظهار كلمة المرور');button.setAttribute('aria-pressed',String(show))};wrapper.append(button);
}}
enhancePasswords();new MutationObserver(enhancePasswords).observe(document.body,{childList:true,subtree:true});
