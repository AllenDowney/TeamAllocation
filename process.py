"""

0) Turn off DUMP_FINAL and DUMP_SWAPS

1) Get students.csv from the Registrar and run

python process.py tokens

This generates token_database.csv

2) Upload the token database to scope-survey.olin.edu and activate
   the survey.

3) When all students have done the survey, download the results to
   survey.csv and run

python process.py summary

4) Print the player cards

python process.py summary > summary
a2ps -B --borders=no -1 -o summary.ps summary; evince summary.ps

5) Run the allocation process

python process.py

6) Remove duplicates

python rmdupes.py

7) To print selected allocations:

python ./process.py 032*.pkl > allocs.txt
a2ps -1 -L100 -B --borders=no -o allocs.ps allocs.txt; evince allocs.ps

8) Turn on DUMP_SWAPS and run

python ./process.py 008.1441721038.212501.pkl > allocs.txt
a2ps -1 -L100 -B --borders=no -o allocs.ps allocs.txt; evince allocs.ps

9) Turn off DUMP_SWAPS and turn on DUMP_FINAL print the final version

python ./process.py 008.1441721038.212501.pkl > allocs.txt

Edit in the trades and send to SCOPE director

"""

#!/usr/bin/python
import sys
import random
import pickle
import time
import csv

from fuzzy import FuzzyDict
from wrap import wrap

DUMP_SWAPS = False
DUMP_FINAL = False

WORTH_SAVING = 28

PROJECT_NAMES = [
    'Name 1',
    'Name 2',
]

# projects that require US citizenship or permanent resident status
RESTRICTED_PROJECTS = [
    'Blue Origin',
]

# minimum and maximum number of students per project
MINSTAFF = 5
MAXSTAFF = 6

# projects allowed to go below the minimum
MINSTAFF_EXCEPTIONS = {
    'Name 1': 4,
}

# projects allowed to go above the maximum
MAXSTAFF_EXCEPTIONS = {
    'Name 2': 5,
}

# projects that don't need allocation
LOCKED_PROJECT_NAMES = [
]

# students locked onto a project
LOCKED_STUDENTS = [
    ('Student 1', 'Name 1'),
]

# students barred from a project
BARRED_STUDENTS = [
    ("Student 1", 'Name 2'),
]


# TODO: implement locked and barred students without modifying
# preferences

SURVEYFILE = 'survey.csv'
STUDENTFILE = 'students.csv'

CONFLICTCOST = 100
PREFCOST = [None, 10000, 1000, 5, 1, 0]
OVERCOST = 100000
UNDERCOST = 100000
GPACOST = 100
NONCITIZENCOST = 1000

SKILL_NAMES = [
    'Machine Shop',
    'Mechanical Design',
    'Programming',
    'ECE hardware design',
    'Math modeling',
    'User-oriented design',
]


def clean(comment):
    """prepare comment field for printing"""
    comment = comment.replace('\\', '')
    comment = wrap(comment, 70)
    return comment


class Mdict(dict):
    """a subclass of the built-in dictionary in which each key
    maps to a list of values.  This class inherits the constructor
    from dict, so it is up to the user to initialize all values to
    lists.
    """
    def __setitem__(self, key, value):
        """add the given value to the list of values for this key"""
        self.setdefault(key, []).append(value)


class Hist(dict):
    """dictionary that maps from items to the number of times
    they appear
    """
    def count(self, x):
        """Increments the count for an item.

        x: item
        """
        self[x] = self.get(x, 0) + 1


class Allocation:
    """an allocation represents an assignment of students to
    teams

    teams: map from Project to a list of students
    ison: maps from a student to the team s/he is on
    projects: list of Project
    skills: list of string skill names
    students: list of Student
    """

    def __init__(self, survey):
        self.teams = {}
        self.ison = {}
        self.projects = survey.projects
        self.skills = survey.skills
        self.conflicts = None

        t = [(stu.last, stu.first, stu) for stu in survey.students.values()]
        t.sort()
        self.students = [stu for (_, _, stu) in t]

        for proj in self.projects:
            self.teams[proj] = []

    def add(self, stu, proj):
        """add stu to proj"""
        assert self.ison.get(stu, None) == None
        self.teams[proj].append(stu)
        self.ison[stu] = proj

    def remove(self, stu, proj):
        """remove stu from proj"""
        assert self.ison[stu] == proj
        self.teams[proj].remove(stu)
        self.ison[stu] = None

    def num(self, proj):
        """return the number of students on proj"""
        return len(self.teams[proj])

    def dump(self, survey):
        """print this allocation"""
        total = 0
        for proj in self.projects:
            print '\n', proj
            team = self.teams[proj]

            t = [(stu.last, stu) for stu in team]
            t.sort()

            for last, stu in t:
                conflicts = self.conflicts.get(stu, [])
                print stu.prefs[proj],
                print stu.entry2(conflicts),

                print skill_string(stu, survey.skills),
                print

            total += len(team)

        print '\nscore =',
        self.score()

    def dump_final(self):
        """print this allocation"""
        for proj in self.projects:
            print '\n', proj
            team = self.teams[proj]

            t = [(stu.last, stu) for stu in team]
            t.sort()

            for last, stu in t:
                print stu.name

    def dump_swaps(self):
        """for each student, print the cheapest swaps and moves"""
        for stu in self.students:
            print stu, self.ison[stu]

            swaps = self.cheapest_swaps(stu)
            for cost, stu2 in swaps:
                proj = self.ison[stu2]
                print '    %d\t%20.20s %8.8s  %s %s' % (cost, stu2,
                                               stu2.major, stu2.gpa,
                                               str(proj)[:30])

            print ''
            moves = self.cheapest_moves(stu)
            for cost, proj in moves:
                print '    %d\t%s' % (cost, str(proj)[:30])

            print ''

    def pickle(self):
        """save this allocation in a pickle file with name
        score.timestamp.pkl"""
        self.note_conflicts()
        score = self.score()
        ts = time.time()
        filename = '%.3d.%.6f.pkl' % (score, ts)
        fp = open(filename, 'wb')
        pickle.dump(self, fp)

    def score(self, flag=False):
        """compute the score for this allocation, printing only
        if flag is true"""

        def enough_on_list(proj, name_list, minimum):
            """Checks whether a team has no E:C"""
            count = 0
            for stu in self.teams[proj]:
                if stu.name in name_list:
                    count += 1
            return count >= minimum

        scores = Hist()
        total = 0

        # map from team size to maximum number of low GPAs
        limit = {4:2, 5:2, 6:3, 7:3, 8:3}

        for proj, stus in self.teams.iteritems():
            # use this to make sure a team gets enough people
            # from a particular list
            #if proj.name == 'Name 1':
            #    if not enough_on_list(proj, SPECIAL_LIST, 2):
            #        total += 100

            # if a project is under or overstaffed, that's bad
            if self.num(proj) < proj.minstaff:
                total += UNDERCOST

            if self.num(proj) > proj.maxstaff:
                total += OVERCOST

            if proj.restricted:
                non_citizens = [stu for stu in stus if not stu.is_citizen]
                n = len(non_citizens)
                if n:
                    if flag:
                        print '%s non citizens on restricted project' % n
                    total += n * NONCITIZENCOST

            # scores counts the number of 3's 4's 5'
            prefs = [stu.prefs[proj] for stu in stus]
            for pref in prefs:
                scores.count(pref)

            # check for GPA violations
            gpas = [stu.gpa for stu in stus if float(stu.gpa) < 3.0]
            try:
                if len(gpas) > limit[len(stus)]:
                    total += GPACOST
                    if flag:
                        print 'Too many GPA<3.0:', proj.name
            except KeyError:
                pass

        # total up the placement scores
        for pref, num in scores.iteritems():
            total += num * PREFCOST[pref]

        # add a penalty for overriding conflicts
        conflicts = self.total_conflicts()
        total += conflicts * CONFLICTCOST

        print scores, '+', conflicts, 'conflicts =', total
        return total

    def total_conflicts(self):
        """how many conflicts are there in the whole allocation?"""
        count = 0
        for proj in self.projects:
            team = self.teams[proj]
            for stu in team:
                for anti in stu.antistus:
                    if anti in team:
                        count += 1
        return count

    def note_conflicts(self):
        """if there are conflicts in this allocation, add them to
        self.conflicts, which maps from each student to a list of
        students on the same team who conflict"""
        self.conflicts = Mdict()
        for proj in self.projects:
            team = self.teams[proj]
            for stu in team:
                for anti in stu.antistus:
                    if anti in team:
                        self.conflicts[stu] = anti

    def fix_conflicts(self):
        """try to fix conflicts"""

        # find all the students with a conflict
        stus = []
        for proj in self.projects:
            team = self.teams[proj]
            for stu in team:
                for anti in stu.antistus:
                    if anti in team:
                        stus.append(stu)
                        break

        # try to swap or move one of them
        random.shuffle(stus)
        for stu in stus:
            count = self.find_swap(stu) or self.find_move(stu)
            if count > 0: return count
        return 0

    def fix_understaff(self):
        """check for projects that are understaffed and move
        students if necessary
        """
        while True:
            # make a list of understaffed projects
            projects = [dest for dest in self.projects
                        if self.num(dest) < dest.minstaff]
            if len(projects) == 0:
                return
            random.shuffle(projects)

            for dest in projects:
                self.add_student(dest)

    def add_student(self, dest):
        """move a student from another project to dest"""
        sources = [src for src in self.projects
                   if self.num(src) > src.minstaff and src is not dest]

        # sources is the list of projects that can spare someone

        stus = []
        for src in sources:
            for stu in self.teams[src]:
                rand = random.random()
                stus.append((stu.prefs[dest], rand, stu))

        # stus is the list of students that can move from src
        # to dest, decorated with preference and a random number

        _, _, stu = max(stus)
        self.move(stu, dest)

    def fix_and_swap(self):
        """start by fixing conflicts and then look for swaps;
        repeat 10 times or until there are no more moves"""
        for _ in range(10):
            self.score()
            swaps = 0
            swaps += self.fix_conflicts()
            swaps += self.find_swaps()
            # print 'made %d swaps\n' % swaps,
            if swaps == 0:
                break

    def find_swaps(self):
        """find all the students who are sad and try to find
        a swap that makes them happy.  Return the total number
        of swaps made.
	"""
	# make a list of (diff, random, student) tuples, in ascending order
        sad = [(stu.prefs[self.ison[stu]]-stu.maxpref, random.random(), stu)
               for stu in self.students]
        sad.sort()

        # try to make each student happier without hurting the global score
        total = 0
        for _, _, stu in sad:
            total += self.find_swap(stu) or self.find_move(stu)
        return total

    def find_swap(self, stu, tol=0):
        """find the first swap that makes this student happier
        and that hurts the score by no more than tol (as tol
        gets large and negative, we accept more moves)
        """
        src = self.ison[stu]
        t = []
        for dest in self.projects:
            if src is dest: continue
            for stu2 in self.teams[dest]:
                total = stu2.prefs[src] + stu.prefs[dest]
                rand = random.random()
                t.append((total, rand, stu2))

        if len(t) == 0:
            return 0
        t.sort(reverse=True)

	# try out the possible swaps in decreasing order of total
        # happiness

        for total, rand, stu2 in t:
            if self.try_swap(stu, stu2):
                return 1

        return 0

    def find_move(self, stu, tol=0):
        """find a project we can move this student to without
        hurting the global score by more than tol
        """
	# find all the projects this student likes better
        src = self.ison[stu]
        pref = stu.prefs[src]
        projects = [(stu.prefs[proj], proj)
                    for proj in self.projects
                    if proj != src]

	# projects is a list of (preference, project) tuples
        for pref, dest in projects:
            if self.try_move(stu, dest):
                return 1

        return 0

    def swap(self, stu1, stu2):
        """swap stu1 and stu2"""
        p1 = self.ison[stu1]
        p2 = self.ison[stu2]
        self.remove(stu1, p1)
        self.remove(stu2, p2)
        self.add(stu1, p2)
        self.add(stu2, p1)


    def move(self, stu, proj):
        """move this student to proj"""
        src = self.ison[stu]
        self.remove(stu, src)
        self.add(stu, proj)


    def enumerate_swaps(self):
        """try all possible swaps and return the number of winners.
        """
        total = 0
        for stu1 in self.students:
            for stu2 in self.students:
                if stu1 is stu2: continue
                total += self.try_swap(stu1, stu2)
        return total

    def enumerate_moves(self):
        """try all possible moves and return the number of winners.
        """
        total = 0
        for stu in self.students:
            for proj in self.projects:
                total += self.try_move(stu, proj)
        return total

    def cheapest_swaps(self, stu1, n=10):
        """find all the possible swaps for this student and return
        a list of (cost, student) tuples"""
        t = [(self.cost_swap(stu1, stu2), stu2)
             for stu2 in self.students
             if self.ison[stu1] is not self.ison[stu2]]
        t = [(cost, stu) for cost, stu in t if cost<100]
        t.sort()
        return t

    def cheapest_moves(self, stu, n=10):
        """find all the possible moves for this student and return
        a list of (cost, project) tuples in increasing order of cost"""
        src = self.ison[stu]
        t = [(self.cost_move(stu, proj), proj)
             for proj in self.projects if proj is not src]
        t = [(cost, proj) for cost, proj in t if cost<100]
        t.sort()
        return t

    def cost(self, stu, proj, exclude=None):
        """what is the cost of having this student on this project,
        given that (exclude) is _not_ on the project"""
        team = self.teams[proj]
        conflicts = [1 for stu2 in stu.antistus
                     if stu2 is not exclude and stu2 in team]

        conflicts += [1 for stu2 in team
                      if stu2 is not exclude and stu in stu2.antistus]

        pref = stu.prefs[proj]
        total = PREFCOST[pref] + CONFLICTCOST * len(conflicts)
        return total

    def cost_swap(self, stu1, stu2):
        """what is the net change in cost of swapping stu1 and stu2"""
        proj1, proj2 = self.ison[stu1], self.ison[stu2]
        before_cost = self.cost(stu1, proj1) + self.cost(stu2, proj2)

        after_cost = (self.cost(stu1, proj2, stu2) +
                      self.cost(stu2, proj1, stu1))

        return after_cost - before_cost

    def try_swap(self, stu1, stu2):
        """check the cost of swapping stu1 and stu2; if it's a win, do it"""
        cost = self.cost_swap(stu1, stu2)

        if cost < 0:
            self.swap(stu1, stu2)
            return 1
        else:
            return 0

    def cost_move(self, stu, dest):
        """what is the net change in cost of moving stu to dest"""
        src = self.ison[stu]
        before_cost = self.cost(stu, src)
        if self.num(src) == src.maxstaff+1: before_cost += OVERCOST
        if self.num(src) == src.minstaff: before_cost -= UNDERCOST

        after_cost = self.cost(stu, dest)
        if self.num(dest) == dest.maxstaff: after_cost += OVERCOST
        if self.num(dest) == dest.minstaff-1: after_cost -= UNDERCOST

        return after_cost - before_cost

    def try_move(self, stu, dest):
        """check the cost of moving stu to dest; if it's a win, do it"""
        cost = self.cost_move(stu, dest)

        if cost < 0:
            self.move(stu, dest)
            return 1
        else:
            return 0

    def desperate(self):
        """for a solution that has no conflicts and no students
        below a 3, move all the students who have 3 and try again"""
        sad = [(stu.prefs[self.ison[stu]], stu) for stu in self.students]
        sad = [stu for pref, stu in sad
               if pref <= 3 and pref < stu.maxpref]
        random.shuffle(sad)

        for stu in sad:
            self.happy(stu)

    def happy(self, stu):
        """make this student happy even if you have to violate a conflict"""
        src = self.ison[stu]
        pref = stu.prefs[src]
        if pref >= 4 or pref >= stu.maxpref:
            return

        projects = [proj for proj in self.projects
                    if stu.prefs[proj] > pref]

        if len(projects) == 0:
            return

        # make a list of possible moves and their costs
        t1 = [(self.cost_move(stu, proj), random.random(), proj)
             for proj in projects]

        stus = []
        for proj in projects:
            stus.extend(self.teams[proj])

        # make a list of possible swaps and their costs
        t2 = [(self.cost_swap(stu, stu2), random.random(), stu2)
             for stu2 in stus]

        # find the cheapest move and the cheapest swap
        cost1, rand, proj = min(t1)
        cost2, rand, stu2 = min(t2)

        # whichever is cheaper, do it
        if cost1 < cost2:
            self.move(stu, proj)
        else:
            self.swap(stu, stu2)



def make_random_alloc(survey):
    """make an allocation by assigning students at random"""
    alloc = Allocation(survey)

    for stu in self.students:
        while 1:
            proj = random.choice(self.projects)
            if alloc.num(proj) < MAXSTAFF:
                break
        alloc.add(stu, proj)

    return alloc


def make_greedy_alloc(survey):
    """make an allocation by traversing the students in a random
    order and placing each on their most preferred project."""

    alloc = Allocation(survey)

    students = alloc.students[:]
    random.shuffle(students)

    for stu in students:

        # make a list of tuples sorted by decreasing preference
        t = []
        for proj in alloc.projects:
            pref = stu.prefs[proj]
            rand = random.random()
            t.append((pref, rand, proj))

        t.sort(reverse=True)

        for pref, rand, proj in t:
            if alloc.num(proj) < MAXSTAFF:
                alloc.add(stu, proj)
                break

        # make sure all students get on a project
        assert alloc.ison[stu]

    return alloc


def make_greedy_alloc2(survey):
    """make an allocation using a greedy algorithm: loop through
    the projects in random order and let each of them choose the
    best fit among the remaining students."""
    alloc = Allocation(survey)

    students = survey.students[:]
    projects = survey.projects[:]

    while 1:
        random.shuffle(projects)

        for proj in projects:
            if alloc.num(proj) >= MAXSTAFF:
                continue

            stus = [(alloc.cost(stu, proj), random.random(), stu)
                    for stu in students]

            cost, rand, stu = min(stus)
            alloc.add(stu, proj)
            students.remove(stu)

            if len(students) == 0:
                return alloc


class Project(object):
    """each project has a name, an index (i), and an Mdict that
    maps from a preference to the list of students that gave this
    project that preference"""

    def __init__(self, name, i):
        self.name = name
        self.i = i
        self.students = Mdict()
        self.minstaff = MINSTAFF_EXCEPTIONS.get(name, MINSTAFF)
        self.maxstaff = MAXSTAFF_EXCEPTIONS.get(name, MAXSTAFF)
        self.restricted = False

    def __str__(self):
        return '%s (%d)' % (self.name, self.i)

    def add(self, student, pref):
        """add a student to this project with the given preference"""
        self.students[pref] = student


class Student(object):
    """Represents a student."""

    def __init__(self, stuid, prefs, roles, skills, email,
                               antinames, comment, major):
        self.stuid = stuid
        self.prefs = prefs
        self.maxpref = max(prefs.values())
        self.roles = roles
        self.skills = skills
        self.email = email
        self.gpa = float('NaN')

        # antinames is a list of strings.
        # antistus is a list of Student objects corresponding to antinames.
        # tally is the number of students who named this one.
        self.antinames = antinames
        self.antistus = []
        self.tally = 0

        self.comment = clean(comment)
        self.major = major
        self.locked = False

    def __str__(self):
        return self.name

    def set_name(self, last, first):
        self.last = last
        self.first = first
        self.name = first + ' ' + last
        self.shortname = first[0] + ' ' + last

    def entry(self, antis):
        """Returns a string representation."""
        name = self.name[:23]
        name = name + ' ' * (23-len(name))
        return '%s  %8.8s  %s  %s  %s' % (name, self.major,
                                              self.roles[0], self.gpa, antis)

    def entry1(self):
        """Returns a short student entry with antipreferences."""
        antis = [stu.shortname for stu in self.antistus]
        return self.entry(antis)

    def entry2(self, conflicts):
        """Returns a short student entry with actual conflicts."""
        antis = [stu.shortname for stu in conflicts]
        return self.entry(antis)


class Token(object):
    """Represents a student in the token database."""


class Tokens(object):
    def __init__(self, filename):
        self.read_students(filename)

    def read_students(self, filename):
        """Reads the student file.

        filename: string
        """
        fp = open(filename)
        reader = csv.reader(fp)
        _title = reader.next()

        self.map = dict()
        self.rows = []

        for line in reader:
            # print line
            token = Token()
            token.first = line[6].strip()
            token.last = line[7].strip()
            token.stuid = line[5].strip()
            try:
                token.gpa = float(line[9])
            except ValueError:
                token.gpa = float('Inf')
            token.citizen = line[12].strip()
            token.visa = line[13].strip()
            token.email = line[14].strip()

            self.map[token.stuid] = token

            row = token.first, token.last, token.email, token.stuid
            self.rows.append(row)

    def lookup(self, stuid):
        return self.map.get(stuid)

    def write_csv(self, filename):
        fp = open(filename, 'w')
        writer = csv.writer(fp)

        header = ['firstname', 'lastname', 'email', 'token']
        writer.writerow(header)

        for row in self.rows:
            writer.writerow(row)

        fp.close()


class Survey(object):
    """Contains the data from the survey."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.project_codes = []
        self.skills = []          # skill names
        self.projects = []        # list of projects

        # students is a fuzzy mapping from names to student objects
        self.students = FuzzyDict(cutoff=0.6)

    def parse(self, filename):
        """Reads the given file and builds the survey."""

        # open the file and read the title line
        fp = open(filename)
        reader = csv.reader(fp)
        titles = reader.next()

        # pull out the project codes
        for title in titles:
            if 'project' in title:
                name = title.split('[')[1]
                name = name.strip(']')
                self.project_codes.append(name)

        # build the list of projects
        self.unlocked_projects = []
        self.locked_projects = []

        i = 1
        for name in PROJECT_NAMES:
            proj = Project(name, i)
            self.unlocked_projects.append(proj)
            i += 1

        for name in LOCKED_PROJECT_NAMES:
            proj = Project(name, i)
            self.locked_projects.append(proj)
            i += 1

        self.projects = self.unlocked_projects + self.locked_projects

        # pull out the skill names
        for title in titles:
            if 'skills' in title:
                skill = title.split('[')[1]
                skill = skill.strip(']')
                self.skills.append(skill)

        n = self.num_prefs = len(self.project_codes)
        m = self.num_skills = len(self.skills)

        #for i, title in enumerate(titles):
        #    print i, title

        # parse the lines
        for t in reader:
            # print t

            survey_id = t[0]
            #completed = t[1]

            j = 1
            i, j = j, j+n
            prefs = t[i:j]

            i, j = j, j+2
            antinames = [name.strip() for name in t[i:j]]

            i, j = j, j+4
            roles = t[i:j]

            i, j = j, j+m
            skills = t[i:j]

            major = t[-5]
            major2 = t[-4]
            comment = t[-3]
            email = t[-2]
	    stuid = t[-1]

            if major2:
                comment = major2 + "\n\n" + comment

            try:
                prefs = [int(x) for x in prefs]
            except ValueError:
                print 'Bad prefs', email, prefs

            # prefs is a map from Project object to int
            prefs = dict(zip(self.projects, prefs))

            # extend prefs with bogus votes for secret projects
            for proj in self.locked_projects:
                prefs[proj] = 1

            stu = Student(stuid, prefs, roles, skills, email,
                          antinames, comment, major)
            self.get_token_info(stu)

            key = stu.name.lower()
            self.students[key] = stu

        # for each project, build the mapping from preferences
        # to students.
        for student in self.students.values():
            for proj, pref in student.prefs.iteritems():
                proj.add(student, pref)

    def get_token_info(self, student):
        """Adds information from STUDENTFILE to the student.

        student: Student object
        """
        token = self.tokens.lookup(student.stuid)
        assert student.email == token.email

        student.gpa = token.gpa
        student.citizen = token.citizen
        student.visa = token.visa

        if (student.citizen == 'UNITED STATES' or
            student.visa == 'Permanent Resident'):
            student.is_citizen = True
        else:
            student.is_citizen = False

        student.set_name(token.last, token.first)

    def process_conflicts(self):
        """For each student, convert from antinames (string)
        to antistus (student objects) using the fuzzy dictionary
        """
        # fixers is a map from known problem names to canonical names
        fixers = {
            }

        stus = self.students.values()
        for stu in stus:
            for name in stu.antinames:
                if name == '': continue
                if name in fixers:
                    name = fixers[name]
                try:
                    stu2 = self.find_student(name)
                    stu.antistus.append(stu2)
                    stu2.tally += 1
                except KeyError:
                    print ('Could not process conflict %s -> %s' %
                           (stu.name, name))

    def fix_whiners(self):
        """
        """
        stus = self.students.values()
        for stu in stus:
            if stu.maxpref < 5:
                print stu.name, stu.maxpref

                for proj, pref in stu.prefs.items():
                    if pref == stu.maxpref:
                        stu.prefs[proj] = 5
                        print proj, stu.prefs[proj]

    def find_student(self, name):
        """Looks up a student by name, returns Student object."""
        stu = self.students[name]
        if stu.name != name:
            print 'Fuzzy match', name, stu.name
        return stu

    def find_project(self, name):
        """Looks up a project by name, returns Project object."""
        for proj in self.projects:
            if proj.name == name:
                return proj
        return None

    def check_citizenship(self):
        """Mark the students who are non-citizens."""
        for stuname in NON_CITIZENS:
            try:
                stu = self.find_student(stuname)
                stu.is_citizen = False
                print stu
            except KeyError:
                print "Can't find non-citizen", stuname

    def check_restrictions(self):
        """Mark the projects that require US citizens."""
        for projname in RESTRICTED_PROJECTS:
            proj = self.find_project(projname)
            if proj == None:
                print "Can't find project %s" % projname
            else:
                print 'restricted', proj
                proj.restricted = True

    def lock_students(self):
        """Lock some students onto a particular project."""
        for stuname, projname in LOCKED_STUDENTS:
            try:
                stu = self.find_student(stuname)
            except KeyError:
                print "Can't find locked student", stuname

            proj = self.find_project(projname)
            if proj == None:
                print "Can't find project %s" % projname
            self.lock_student(stu, proj)

    def lock_student(self, stu, goodproj):
        """Lock a student onto a particular project."""
        print stu.name, 'locked onto', goodproj.name
        stu.locked = True
        for proj in stu.prefs:
            if proj == goodproj:
                stu.prefs[proj] = 5
            else:
                stu.prefs[proj] = 1

    def bar_noncitizens(self):
        """Change the preferences of noncitizens so they will not
        be put on restricted projects.
        """
        for stu in self.students.values():
            for proj in self.projects:
                if proj.restricted and not stu.is_citizen:
                    self.bar_student(stu, proj)

    def bar_students(self):
        """Bar some students from a particular project."""
        for stuname, projname in BARRED_STUDENTS:
            try:
                stu = self.find_student(stuname)
            except KeyError:
                print "Can't find locked student", stuname

            proj = self.find_project(projname)
            if proj == None:
                print "Can't find project %s" % projname
            self.bar_student(stu, proj)

    def bar_student(self, stu, proj):
        """Change the preferences of a noncitizen so they will not
        be put on restricted projects.
        """
        old_pref = stu.prefs[proj]
        print stu, old_pref, 'barred from', proj
        stu.prefs[proj] = 1

    def print_conflicts(self):
        """Print the students who drew the most antipreferences."""
        t = [(stu.tally, stu) for stu in self.students.values()]
        t.sort(reverse=True)

        for tally, stu in t:
            if tally:
                print tally, stu

        print '\n'

    def print_roles(self):
        """Print a summary of the roles students signed up for."""
	d = {}
	for stu in self.students.values():
	    role = stu.roles[0]
	    d[role] = d.get(role, 0) + 1

	t = [(total, role) for role, total in d.iteritems()]
	t.sort(reverse=True)
	for total, role in t:
	    print role, total
        print ''

    def print_hard_to_place(self):
        print 'Hard to place:'
	for stu in self.students.values():
            if stu.locked:
                continue
	    prefs = stu.prefs.values()
            prefs.sort(reverse=True)
            if prefs[1] < 3:
                self.print_student(stu)
        print ''

    def print_students(self):
        """Print one summary page per student."""
        t = [(stu.last, stu.first, stu) for stu in self.students.values()]
        t.sort()

        for last, first, stu in t:
            self.print_student(stu)

    def print_student(self, stu):
        """Prints a summary of the given student."""
        print stu.entry1()

        print ' ' * 30, skill_string(stu, self.skills)

        print '\nProject\t',
        for i in range(len(self.projects)):
            print i+1,
        print '\n     \t',
        for i, proj in enumerate(self.projects):
            try:
                print stu.prefs[proj],
                if i > 8:
                    print '',
            except KeyError:
                print ' ',
        if stu.locked:
            print 'locked',
        print '\n'

        print stu.comment
        print ''

    def print_projects(self):
        """print one summary page per project
        """
        for proj in self.projects:
            print 'Project', proj
            for i in [5, 4, 3]:
                if i in proj.students:
                    print i
                    for student in proj.students[i]:
                        print student.entry1()
            print ''


    def print_names(self):
        """print the project names and skill names"""
        for proj in self.projects:
            print proj.i, '\t', proj.name

        print ''

        for i, skill in enumerate(self.skills):
            print i+1, '\t', skill

        print ''


def skill_string(stu, skill_names):
    skills = []
    for skill, response in zip(skill_names, stu.skills):
        if response == 'Y':
            skills.append(skill)
    return ' '.join(skills)


def generate_alloc(survey, n=10):
    """generate greedy allocations, return the one with lowest cost
    """
    best = (float('Inf'), None)

    for i in range(n):
        alloc = make_greedy_alloc(survey)
        alloc.fix_understaff()
        score = alloc.score()

        if (score, alloc) < best:
            best = (score, alloc)

    return best


def make_survey():
    """read the survey data and populate the global variable survey"""
    tokens = Tokens(STUDENTFILE)
    survey = Survey(tokens)
    survey.parse(SURVEYFILE)

    survey.process_conflicts()
    #survey.fix_whiners()

    # we are not using check_citizenship any more;
    # instead using info from students.csv
    # survey.check_citizenship()

    survey.check_restrictions()
    survey.bar_noncitizens()
    survey.lock_students()
    survey.bar_students()
    return survey


def optimize():
    """run an infinite loop that generates allocations and tries
    to improve them, recording good solutions as it goes.
    """

    survey = make_survey()
    if len(survey.students) < 10:
        print 'Not enough students.'
        sys.exit()

    best = (float('Inf'), None)

    while 1:
        # print 'generating new allocation'
        score, alloc = generate_alloc(survey, 1)
        prev = score

        # keep trying to improve it as long as it keeps getting better
        while 1:
            swaps = alloc.fix_and_swap()
            score = alloc.score()

            if score <= WORTH_SAVING:
                alloc.pickle()

            if score >= prev:
                # print 'no help'
                break
            else:
                # print 'that helped'
                prev = score

            if (score, alloc) < best:
                best = (score, alloc)
            print 'best so far is %d\n' % best[0],

            # print 'taking desperate measures'
            alloc.desperate()


def print_allocations(filenames, dump_swaps=False):
    """filenames is a list of pickle files.  Read each file and
    dump the allocation"""
    survey = make_survey()
    print ''
    print ''

    for filename in filenames:
        fp = open(filename, 'rb')
        alloc = pickle.load(fp)

        if DUMP_FINAL:
            alloc.dump_final()
            print ''
            continue

        print ''
        print ''
        print filename
        alloc.dump(survey)
        print ''
        if dump_swaps:
            alloc.dump_swaps()


def print_summary():
    """print a summary of the survey data"""
    survey = make_survey()
    print ''
    survey.print_names()
    survey.print_conflicts()
    survey.print_roles()
    #survey.print_hard_to_place()
    survey.print_students()
    survey.print_projects()


def process_tokens():
    """
    """
    tokens = Tokens(STUDENTFILE)
    tokens.write_csv('token_database.csv')


def main(script, *args):
    if len(args) == 0:
        try:
            optimize()
        except KeyboardInterrupt:
            print 'done'

    elif args[0] == 'tokens':
        process_tokens()
    elif args[0] == 'summary':
        print_summary()
    else:
        print_allocations(args, DUMP_SWAPS)


if __name__ == '__main__':
    profile = 0
    if profile:
        import profile
        profile.run('main(*sys.argv)')
    else:
        main(*sys.argv)
