import easycentral_keepa_bot as bot
import easycentral_target as target
from keepa_check import open_cdp_session
import time

tab = target._find_existing_tab(bot.DEBUG_ADDRESS)
tab2, session = open_cdp_session(bot.DEBUG_ADDRESS, tab=tab)
session.call('Page.navigate', {'url': 'https://app.easycentral.com/order/list?date_from=2025-09-18&date_to=2026-09-18'})
time.sleep(3)

expr = """
(function(){
  var selects = Array.from(document.querySelectorAll('select'));
  return selects.map(function(s){
    return {name: s.name||s.id||'?', options: Array.from(s.options).map(function(o){return o.value+'='+o.text;})};
  });
})()
"""
print(session.eval_json(expr))
session.close()
