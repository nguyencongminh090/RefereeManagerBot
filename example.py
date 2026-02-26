from seleniumrequests import Firefox
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from parse import parse
import threading
import time
from datetime import datetime, timedelta
import pytz

def get_prague_time(offset_minutes=0):
    prague_tz = pytz.timezone("Europe/Prague")
    current_time = datetime.now(prague_tz) + timedelta(minutes=offset_minutes)
    return current_time.strftime("%H:%M")

LANG_ALT = {
    'eng': 'en',
    'hun': 'hu',
    'cs': 'cz',
    'cze': 'cz'
}

RULES_TEXT = {
    'en': 'To remind, you play a total of 15 games. You may take a 5 minute break after 10 games if at least one of you wants to. Good luck & Have fun!',
    'hu': 'Emlékeztetésképp: 15 játékból áll a meccs. 10 játék után lehet 5 perc szünetet tartani, ha legalább egyikőtök szeretné. Sok sikert és jó játékot!',
    'cz': 'Pro připomenutí: hrajete celkem 15 her. Po 10 hrách je možné si dát 5 minut přestávku, pokud alespoň jeden z vás bude mít zájem.'
}
    
BREAK_TEXT = 'Break! It is {curr_time} now, please continue the match not later than {resume_time}.'

PLAYOK_USERNAMES = ['wbcbot', 'xoxoai', 'videorecorder']
PLAYOK_PASSWORDS = ['bambam123', 'jahxoxo1503', 'bambam123']

class CountingAgent:
    def __init__(self, username, password):
        self.username = username
        self.password = password

        self.score = [0, 0]
        self.last_scored = 0
        self.last_message = ''
        self.last_message_id = 0

        self.thread = threading.Thread(target=self.run, daemon=True)
        self.exit_event = threading.Event()
        self.thread.start()

    def playok_get_new_messages(self):
        try:
            msgs = list(self.driver.find_elements(By.CSS_SELECTOR, 'div.bsbb.tsbinner div.tind'))
        except Exception:
            return []
        if not msgs:
            self.last_message = ''
            self.last_message_id = 0
            return []
        if self.last_message_id == len(msgs):
            return []
        if self.last_message_id > len(msgs) or self.last_message_id == 0 or msgs[self.last_message_id - 1].text.strip() != self.last_message:
            self.last_message = msgs[-1].text.strip()
            self.last_message_id = len(msgs)
            return []
        last_msg_id = self.last_message_id
        self.last_message = msgs[-1].text.strip()
        self.last_message_id = len(msgs)
        return [msgs[i].text.strip() for i in range(last_msg_id, len(msgs))]

    def playok_send_message(self, message):
        try:
            chat_input = self.driver.find_element(By.CSS_SELECTOR, 'div.bsbb.tsbinner form input')
            chat_input.send_keys(message)
            chat_input.send_keys(Keys.RETURN)
        except Exception:
            print('Send message failed')

    def parse_message(self, message):
        p = parse('+ {msg}', message)
        if p:
            return ('+', p['msg'])
        p = parse('{who}: {msg}', message)
        if p:
            return (p['who'], p['msg'])

    def show_score(self):
        self.playok_send_message(f'{self.score[0]:g}-{self.score[1]:g}')

    def show_info_message(self):
        if sum(self.score) >= 15:
            self.playok_send_message('Ggs')
        elif sum(self.score) == 14:
            self.playok_send_message('Last game')
        elif sum(self.score) % 10 == 9:
            self.playok_send_message('You may take a break after the next game.')

    def score_change(self):
        self.show_score()
        self.show_info_message()

    def add_game(self, result):
        self.score[0] += result
        self.score[1] += 1 - result
        self.last_scored = 1 if result == 1 else 2 if result == 0 else 0
        self.score_change()

    def set_score(self, home, away):
        self.score[0] = home
        self.score[1] = away
        self.last_scored = 0
        self.score_change()

    def process_message(self, who, message):
        print(f'[{who}]: {message}')
        if who == self.username:
            return
        if who == '+':
            if message == 'player #1 wins':
                self.add_game(1)
            elif message == 'player #2 wins':
                self.add_game(0)
            elif message == 'draw':
                self.add_game(0.5)
        else:
            if who in ADMINS:
                p = parse('!set {h:g}-{a:g}', message)
                if p:
                    self.set_score(p['h'], p['a'])
                p = parse('!rules {lang}', message) or parse('!rules', message)
                if p:
                    lng = p['lang'] if 'lang' in p else 'en'
                    lng = LANG_ALT.get(lng, lng)
                    if lng in RULES_TEXT:
                        self.playok_send_message(RULES_TEXT[lng])
                p = parse('!break', message)
                if p:
                    curr_time = get_prague_time()
                    resume_time = get_prague_time(5)
                    self.playok_send_message(BREAK_TEXT.format(curr_time=curr_time, resume_time=resume_time))
                p = parse('!leave', message)
                if p:
                    self.playok_send_message('see you')
                    self.close_table()
                    return 'leave'
            if message == '!show' or message == '!score':
                self.show_score()

    def do_counting(self):
        print('Counting...')
        self.score = [0, 0]
        self.last_message = ''
        self.last_message_id = 0
        while not self.exit_event.is_set():
            messages = self.playok_get_new_messages()
            for msg in messages:
                retval = self.process_message(*self.parse_message(msg))
                if retval == 'leave':
                    return
            time.sleep(0.1)

    def close_table(self):
        print(f'Leaving table')
        self.driver.find_element(By.CSS_SELECTOR, 'div.ttlnav button.butlh:nth-child(2)').click()

    def login(self):
        self.driver.get('https://www.playok.com/en/gomoku/')
        time.sleep(5)

        accept_cookies_button = self.driver.find_element(By.CSS_SELECTOR, 'button[mode="primary"]')
        accept_cookies_button.click()
        time.sleep(1)

        login_button = self.driver.find_element(By.CLASS_NAME, 'lbpbg')
        login_button.click()
        time.sleep(1)

        username_input = self.driver.find_element(By.CSS_SELECTOR, 'input[name="username"]')
        password_input = self.driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
        submit_button = self.driver.find_element(By.CSS_SELECTOR, 'form button')
        username_input.send_keys(self.username)
        password_input.send_keys(self.password)
        time.sleep(1)
        submit_button.click()
        time.sleep(1)

        start_button = self.driver.find_element(By.CSS_SELECTOR, 'button.lbprm')
        start_button.click()
        time.sleep(1)

    def go_to_dobrocin(self):
        select = self.driver.find_element(By.CSS_SELECTOR, 'select.selcsl')
        select.click()
        time.sleep(1)

        option = self.driver.find_element(By.CSS_SELECTOR, 'select.selcsl > option:nth-child(3)')
        option.click()
        time.sleep(1)

    def wait_for_invite(self):
        print('Waiting for invite...')
        while not self.exit_event.is_set():
            elems = self.driver.find_elements(By.CSS_SELECTOR, 'div.alrt.dcpd > div.mbsp')
            if not elems:
                continue
            txt = elems[0].text.strip()
            p = parse('{user} [{elo:d}] invites you to table #{table:d} {info}; accept?', txt)
            if not p: 
                continue
            print(f'Invite received')
            if p['user'] in ADMINS:
                accept_button = self.driver.find_element(By.CSS_SELECTOR, 'div.alrt.dcpd > button.minw:nth-of-type(1)')
                accept_button.click()
                print('Table entered')
                time.sleep(1)
                self.playok_send_message('hi')
                break
            reject_button = self.driver.find_element(By.CSS_SELECTOR, 'div.alrt.dcpd > button.minw:nth-of-type(2)')
            reject_button.click()
            time.sleep(0.1)

    def run(self):
        self.driver = Firefox()

        self.login()
        self.driver.switch_to.window(self.driver.window_handles[1])
        self.go_to_dobrocin()

        while not self.exit_event.is_set():
            self.wait_for_invite()
            self.do_counting()

        self.driver.quit()

    def exit(self):
        self.exit_event.set()
        self.thread.join()

    def join(self):
        self.thread.join()

ADMINS = open('admins.txt').read().strip().split()

def main():
    unames = '/'.join(PLAYOK_USERNAMES)
    idx = int(input(f'Select username ({unames}): '))
    agent = CountingAgent(PLAYOK_USERNAMES[idx], PLAYOK_PASSWORDS[idx])
    agent.join()


if __name__ == '__main__':
    main()
