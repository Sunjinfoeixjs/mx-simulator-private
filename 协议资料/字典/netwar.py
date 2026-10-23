# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.8.10 (tags/v3.8.10:3d8993a, May  3 2021, 11:48:03) [MSC v.1928 64 bit (AMD64)]
# Embedded file name: script\netwar.py
import net, war, prop
g_WarTimes = 0

def C2SWarPerform(id, perform):
    net.g_Net.PacketPrepare(net.C2S_WAR_PERFORM)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketAddI(perform, 2)
    net.g_Net.PacketSend()
    return


def C2SWarLook(id):
    net.g_Net.PacketPrepare(net.C2S_WAR_LOOK)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketSend()
    return


def C2SWarAttack(id):
    net.g_Net.PacketPrepare(net.C2S_WAR_ATTACK)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketSend()
    return


def C2SWarDefend():
    net.g_Net.PacketPrepare(net.C2S_WAR_DEFEND)
    net.g_Net.PacketSend()
    return


def C2SWarEscape():
    net.g_Net.PacketPrepare(net.C2S_WAR_ESCAPE)
    net.g_Net.PacketSend()
    return


def C2SWarItem(id, item):
    net.g_Net.PacketPrepare(net.C2S_WAR_ITEM)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketAddI(item, 4)
    net.g_Net.PacketSend()
    return


def C2SWarSummon(op, id):
    net.g_Net.PacketPrepare(net.C2S_WAR_SUMMON)
    net.g_Net.PacketAddI(op, 1)
    if op == 0:
        net.g_Net.PacketAddI(id, 4)
    net.g_Net.PacketSend()
    return


def C2SWarProtect(id):
    net.g_Net.PacketPrepare(net.C2S_WAR_PROTECT)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketSend()
    return


def C2SWarCatch(id):
    net.g_Net.PacketPrepare(net.C2S_WAR_CATCH)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketSend()
    return


def C2SChild():
    net.g_Net.PacketPrepare(net.C2S_WAR_SUMMONCHILD)
    net.g_Net.PacketSend()
    return


def C2SWarSummAttack(id, target):
    net.g_Net.PacketPrepare(net.C2S_WAR_SUMM_ATTACK)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketAddI(target, 1)
    net.g_Net.PacketSend()
    return


def C2SWarSummPerform(id, target, perform):
    net.g_Net.PacketPrepare(net.C2S_WAR_SUMM_PERFORM)
    net.g_Net.PacketAddI(target, 1)
    net.g_Net.PacketAddI(perform, 2)
    net.g_Net.PacketSend()
    return


def C2SWarSummGuard(id):
    net.g_Net.PacketPrepare(net.C2S_WAR_SUMM_GUARD)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketSend()
    return


def C2SWarSummEscape(id):
    net.g_Net.PacketPrepare(net.C2S_WAR_SUMM_ESCAPE)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketSend()
    return


def C2SWarSummItem(id, target, item):
    net.g_Net.PacketPrepare(net.C2S_WAR_SUMM_ITEM)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketAddI(target, 1)
    net.g_Net.PacketAddI(item, 4)
    net.g_Net.PacketSend()
    return


def C2SWarSummProtect(id, target):
    net.g_Net.PacketPrepare(net.C2S_WAR_SUMM_PROTECT)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketAddI(target, 1)
    net.g_Net.PacketSend()
    return


def StartRecord(netobj):
    print 'StartRecord'
    if not hasattr(netobj, 'CreateRecordFile'):
        return
    if war.g_RecordWar:
        try:
            f = open('record\\war.ini', 'rt')
            idx = int(f.read().split()[0])
        except:
            try:
                import os
                os.mkdir('record')
            except:
                pass
            else:
                idx = 0
        else:
            idx = (idx + 1) % 1000
            try:
                f = open('record\\war.ini', 'wt')
                f.write(str(idx))
                name = 'record\\%.4d.mwr' % idx
                netobj.CreateRecordFile(name, 4)
                netobj.WriteRecordFile()
                if war.g_FirstRecordWar:
                    import usermacro
                    usermacro.PROMPT_MSG(b'\xcf\xd6\xd4\xda\xb4\xa6\xd3\xda\xd3\xce\xcf\xb7\xc2\xbc\xcf\xf1\xd6\xd0\xa3\xac\xc2\xbc\xcf\xf1\xce\xc4\xbc\xfe\xbf\xc9\xd2\xd4\xd4\xda\xd3\xce\xcf\xb7\xb0\xb2\xd7\xb0\xc4\xbf\xc2\xbc\xb5\xc4\xa1\xb0record\xa1\xb1\xce\xc4\xbc\xfe\xbc\xd0\xd6\xd0\xd5\xd2\xb5\xbd')
                    war.g_FirstRecordWar = False
            except IOError:
                war.g_RecordWar = False

    else:
        netobj.CloseRecordFile()
    return


def S2CWarStart(netobj):
    global g_WarTimes
    StartRecord(netobj)
    g_WarTimes += 1
    cmd = war.CCmd(war.Start)
    cmd.scene = netobj.UnpackInt(2) + 1001
    cmd.alineup = netobj.UnpackInt(1)
    cmd.elineup = netobj.UnpackInt(1)
    cmd.type = netobj.UnpackInt(1)
    cmd.x = netobj.UnpackInt(2)
    cmd.y = netobj.UnpackInt(2)
    print '! S2CWarStart scene=%d alineup=%d elineup=%d type=%d x=%d y=%d' % (
     cmd.scene, cmd.alineup, cmd.elineup, cmd.type, cmd.x, cmd.y)
    import main, mainmenu
    try:
        mainmenu.SwitchEnv(IsWar=True)
    except AttributeError:
        pass

    main.SetWarLoop()
    war.SetProcess(war.NextCmd)
    return


def S2CWarEnd(netobj):
    global g_WarTimes
    print '! S2CWarEnd'
    if g_WarTimes == 0:
        return
    g_WarTimes -= 1
    cmd = war.CCmd(war.End)
    war.g_Turn = war.g_Turn + 1
    war.g_Weather = 0
    net.g_Net.Pause(1)
    return


def S2CWarTurn(netobj):
    print '! S2CWarTurn'
    cmd = war.CCmd(war.Turn)
    if not netobj.UnpackInt(1):
        cmd.lastcmd = False
    else:
        cmd.lastcmd = True
    try:
        if not war.g_ReplayWar:
            import warui
            warui.Hide()
    except:
        pass

    war.g_Turn = war.g_Turn + 1
    import warui
    if war.g_HeroID == None:
        import warui
        warui.g_MagicKeyDlg.SetShow(False)
    return


def S2CWarAdd(netobj):
    cmd = war.CCmd(war.Add)
    cmd.id = netobj.UnpackInt(1)
    cmd.shape = netobj.UnpackInt(2)
    if war.g_ReplayVer == 1:
        cmd.desc = netobj.UnpackInt(2)
    elif war.g_ReplayVer == 2:
        cmd.desc = netobj.UnpackInt(4)
    else:
        cmd.desc = prop.UnpackDesc(netobj)
        cmd.desc['horse'] = None
    cmd.allied = netobj.UnpackBit(0, 1)
    cmd.type = netobj.UnpackBit(1, 1)
    cmd.child = netobj.UnpackBit(2, 1)
    _ = netobj.UnpackBit(3, 5)
    cmd.owner_id = netobj.UnpackInt(1)
    if war.g_ReplayVer in (0, 4):
        cmd.cls = netobj.UnpackInt(1)
        cmd.grade = netobj.UnpackInt(1)
    else:
        cmd.cls = 255
        cmd.grade = 255
    cmd.name = netobj.UnpackString(0)
    cmd.leader = 0
    cmd.place = 0
    print '! S2CWarAdd ID:%d shape=%d allied=%d leader=%d type=%d place=%d owner_id=%d name=%s' % (cmd.id,
     cmd.shape, cmd.allied, cmd.leader, cmd.type, cmd.place, cmd.owner_id, cmd.name), cmd.desc
    return


def S2CWarDel(netobj):
    cmd = war.CCmd(war.Delete)
    cmd.id = netobj.UnpackInt(1)
    print '! S2CWarDel ID:%d' % cmd.id
    return


def S2CWarAttackNormal(netobj):
    cmd = war.CCmd(war.AttackNormal)
    cmd.att_id = netobj.UnpackInt(1)
    cmd.vic_id = netobj.UnpackInt(1)
    print '! S2CWarAttackNormal( attacker_id:%d victim_id=%d' % (cmd.att_id, cmd.vic_id)
    return


def GS2CWarShape(netobj):
    cmd = war.CCmd(war.ChgShape)
    cmd.id = netobj.UnpackInt(1)
    cmd.shape = netobj.UnpackInt(2)
    if war.g_ReplayVer == 1:
        cmd.desc = netobj.UnpackInt(2)
    elif war.g_ReplayVer == 2:
        cmd.desc = netobj.UnpackInt(4)
    else:
        cmd.desc = prop.UnpackDesc(netobj)
        cmd.desc['horse'] = None
    cmd.name = netobj.UnpackString(0)
    print '! GS2CWarShape( id:%d shape=%d name=%s' % (cmd.id, cmd.shape, cmd.name)
    return


def S2CWarAttackNormalEnd(netobj):
    cmd = war.CCmd(war.AttackNormalEnd)
    cmd.id = netobj.UnpackInt(1)
    print '! S2CWarAttackNormalEnd'
    return


def S2CWarAttackConverg(netobj):
    cmd = war.CCmd(war.AttackConverg)
    cmd.att_id = netobj.UnpackInt(1)
    cmd.vic_id = netobj.UnpackInt(1)
    print '! S2CWarAttackConverg attacker_id:%d victim_id=%d' % (cmd.att_id, cmd.vic_id)
    return


def S2CWarProtect(netobj):
    cmd = war.CCmd(war.Protect)
    cmd.protector_id = netobj.UnpackInt(1)
    cmd.protege_id = netobj.UnpackInt(1)
    print '! S2CWarPROTECT saver_id:%d id=%d' % (cmd.protector_id, cmd.protege_id)
    return


def S2CWarPerform(netobj):
    cmd = war.CCmd(war.Perform)
    cmd.id = netobj.UnpackInt(1)
    cmd.pid = netobj.UnpackInt(2)
    cmd.vic_ids = []
    for i in xrange(netobj.GetCmdLen() - 3):
        cmd.vic_ids.append(netobj.UnpackInt(1))

    print '! S2CWarPerform id=%d pid=%d  victim_id:' % (cmd.id, cmd.pid), cmd.vic_ids
    return


def S2CWarWState(netobj):
    cmd = war.CCmd(war.State)
    cmd.id = netobj.UnpackInt(1)
    cmd.hurt = netobj.UnpackBit(0, 4)
    cmd.life = netobj.UnpackBit(4, 2)
    cmd.ward = netobj.UnpackBit(6, 2)
    cmd.hp_chg = netobj.UnpackInt(4)
    print '! S2CWarWState id:%d hurt=%d life=%d ward=%d hp_chg=%d' % (cmd.id, cmd.hurt, cmd.life, cmd.ward, cmd.hp_chg)
    return


def S2CWarAddon(netobj):
    cmd = war.CCmd(war.Addon)
    cmd.id = netobj.UnpackInt(1)
    cmd.pid = netobj.UnpackInt(2)
    cmd.turn = netobj.UnpackInt(1)
    cmd.series = netobj.UnpackInt(1)
    print '! S2CWarADDON id=%d pid=%d turn=%d' % (cmd.id, cmd.pid, cmd.turn)
    return


def S2CWarWeather(netobj):
    cmd = war.CCmd(war.Weather)
    cmd.weather = netobj.UnpackInt(1)
    war.g_Weather = cmd.weather
    print '! S2CWarWeather weather=%d' % cmd.weather
    return


def S2CWarPlayerChat(netobj):
    cmd = war.CCmd(war.PlayerChat)
    cmd.channel = netobj.UnpackInt(1)
    if cmd.channel == 2:
        cmd.direction = netobj.UnpackInt(1)
    else:
        cmd.direction = 2
    cmd.word = netobj.UnpackString(0)
    print '! S2CWarPlayerChat  channel=%d direction=%d word=%s' % (cmd.channel, cmd.direction, cmd.word)
    return


def S2CWarHPPercent(netobj):
    cmd = war.CCmd(war.HPPercent)
    cmd.id = netobj.UnpackInt(1)
    cmd.per = netobj.UnpackInt(1)
    print '! S2CWarSTATUS id:%d status=%d' % (cmd.id, cmd.per)
    return


def S2CWarSummon(netobj):
    cmd = war.CCmd(war.Summon)
    cmd.id = netobj.UnpackInt(1)
    cmd.op = netobj.UnpackInt(1)
    print '! S2CWarSUMMON id=%d' % cmd.id
    return


def S2CWarSummonCatch(netobj):
    cmd = war.CCmd(war.Catch)
    cmd.id = netobj.UnpackInt(1)
    cmd.sumid = netobj.UnpackInt(1)
    cmd.success = netobj.UnpackInt(1)
    print '! S2CWarSummonCatch id=%d sumid=%d success=%d' % (cmd.id, cmd.sumid, cmd.success)
    return


def S2CWarONACT(netobj):
    import world
    id = netobj.UnpackInt(1)
    import world
    obj = world.g_Objects.get(id)
    if obj:
        obj.m_WaitingTimePercent = 100
    return


def C2SWarSpecial(id, sid):
    net.g_Net.PacketPrepare(net.C2S_WAR_SPECIAL)
    net.g_Net.PacketAddI(id, 1)
    net.g_Net.PacketAddI(sid, 2)
    net.g_Net.PacketSend()
    return


def S2CWarSign(netobj):
    msg = netobj.UnpackString(0)
    print 'S2CWarSign = %s' % msg
    import warui
    warui.OnSign(msg)
    return


def C2SSendSign(msg):
    print 'send sign %s' % msg
    netobj = net.g_Net
    netobj.PacketPrepare(net.C2S_WAR_TEAM)
    netobj.PacketAddS(msg, len(msg))
    netobj.PacketSend()
    return


def C2SLookWarrior(order):
    print 'look warrior'
    netobj = net.g_Net
    netobj.PacketPrepare(net.C2S_WAR_LOOK)
    netobj.PacketAddI(order, 1)
    netobj.PacketSend()
    return


def S2CWarChildSkill(netobj):
    grade = netobj.UnpackInt(1)
    war.g_ChildGrade = grade
    war.g_ChildSkills = []
    cnt = netobj.UnpackInt(1)
    for i in xrange(cnt):
        skid = netobj.UnpackInt(2)
        war.g_ChildSkills.append(skid)

    import pet
    ob = pet.GetActivatePet()
    if ob and isinstance(ob, pet.CChild):
        ob.SetProp('m_Grade', grade)
        ob.UpdateSkill(war.g_ChildSkills)
    return


def S2CWarCalledPet(netobj):
    import warui
    warui.g_CallPetLeft = netobj.UnpackInt(1)
    canCallChild = netobj.UnpackInt(1)
    warui.g_CanCallChild = canCallChild
    calledPetNum = netobj.UnpackInt(1)
    calledPet = []
    for i in xrange(calledPetNum):
        calledPet.append(netobj.UnpackInt(4))

    warui.g_CallPets = calledPet
    return


def S2CChangeAction(netobj):
    cmd = war.CCmd(war.ChgAction)
    cmd.id = netobj.UnpackInt(1)
    cmd.action = netobj.UnpackInt(1)
    print '! S2CChangeAction id=%d, action=%d' % (cmd.id, cmd.action)
    return


return
